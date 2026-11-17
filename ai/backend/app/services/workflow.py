"""Three fixed LangGraph nodes; SQLite steps are durable, versioned result checkpoints."""
from contextlib import closing
import hashlib
import json
import time
from typing import TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph

from app import db
from app.agents import reading, verification, writing
from app.agents.client import Completion, ModelError
from app.config import PROJECT_ROOT, Settings
from app.schemas import Note, Run
from app.services.reading_runs import ReadingWorker, get_run, note_from_row, run_from_row, safe_error
from app.services.retrieval import context, retrieve
from app.services.verification_runs import execute_verification


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def config_digest(settings: Settings) -> str:
    return digest({'base': settings.model_base_url, 'model': settings.model_name,
                   'credential': hashlib.sha256(settings.model_api_key.encode()).hexdigest()})


def cache_key(settings: Settings, role: str, scope: dict, note: Note | None, evidence) -> str:
    versions = {'reading': [reading.PROMPT_VERSION], 'writing': [writing.PROMPT_VERSION],
                'verification': [verification.PROMPT_VERSION, verification.REVISION_PROMPT_VERSION]}[role]
    return digest({'schema': 1, 'role': role, 'config': config_digest(settings),
        'file': {key: scope[key] for key in ('sha256', 'parse_version')},
        'scope': {key: scope.get(key) for key in ('section', 'query', 'conditions', 'revision_limit')},
        'prompts': {version: hashlib.sha256((PROJECT_ROOT/f'prompts/{version}.md').read_bytes()).hexdigest() for version in versions},
        'note': note.model_dump(mode='json') if note else None,
        'evidence': [block.model_dump(mode='json') for block in evidence],
        **({'verification_batch_size': 7} if role == 'verification' else {})})


def account(output: dict, reply: Completion, *, usage_complete: bool | None = None):
    output['known_api_calls'] += reply.calls
    output['api_calls'] = None if output.get('unknown_calls') else output['known_api_calls']
    if reply.usage:
        usage = output['usage'] or {}
        for key, value in reply.usage.items():
            usage[key] = usage.get(key, 0) + value
        output['usage'] = usage
    if usage_complete is False or usage_complete is None and (reply.usage is None and reply.calls or reply.calls > 1):
        output['usage_complete'] = False


def latest_id(conn, document_id) -> str | None:
    row = conn.execute('SELECT id FROM notes WHERE document_id=? ORDER BY version DESC LIMIT 1', (str(document_id),)).fetchone()
    return row['id'] if row else None


def retry(settings: Settings, run_id: UUID) -> Run:
    missing, errors = settings.model_status()
    if missing or errors:
        raise ModelError('模型配置缺失或无效：' + '、'.join(missing + errors))
    with db.transaction(settings.db_path) as conn:
        row = conn.execute('SELECT * FROM runs WHERE id=?', (str(run_id),)).fetchone()
        if row is None:
            raise LookupError('工作流任务不存在')
        run = run_from_row(row)
        if run.input_scope.get('role') != 'workflow' or run.status != 'failed' or run.input_scope['resume_count'] >= 1:
            raise ValueError('仅失败的工作流可恢复一次；请按需启动新流程')
        if run.input_scope['config_digest'] != config_digest(settings):
            raise ValueError('模型配置已变化，请启动新流程；旧结果保留')
        active = conn.execute("SELECT document_id FROM runs WHERE status IN ('pending','running')").fetchall()
        if len(active) >= 10 or any(row['document_id'] == str(run.document_id) for row in active):
            raise ValueError('该文档已有活动任务或队列已满（最多10项）')
        output = run.output or {}
        node = output.get('nodes', {}).get('verification', {})
        # A child can commit just before a parent process stops: adopt only its exact latest result.
        if node.get('child_run_id') and node.get('status') != 'succeeded':
            child_row = conn.execute('SELECT * FROM runs WHERE id=?', (node['child_run_id'],)).fetchone()
            child = run_from_row(child_row) if child_row else None
            if child and child.status == 'succeeded' and child.input_scope.get('parent_run_id') == str(run.id):
                child_note = child.output['note_id']
                if latest_id(conn, run.document_id) == child_note:
                    if not node.get('accounted'):
                        account(output, Completion('', child.output.get('usage'), child.output['api_calls']),
                                usage_complete=child.output.get('usage_complete', False))
                    node.update(status='succeeded', note_id=child_note, accounted=True, error=None)
                    output.update(note_id=child_note, checks=child.output['checks'], revision_rounds=child.output['revision_rounds'],
                                  unresolved_item_ids=child.output['unresolved_item_ids'])
                    conn.execute("UPDATE run_steps SET status='succeeded',error=NULL,output=? WHERE run_id=? AND role='verification' AND attempt=?",
                                 (json.dumps(node), str(run.id), node['attempt']))
        if latest_id(conn, run.document_id) != output.get('note_id', run.input_scope['base_note_id']):
            raise ValueError('笔记已有新版本，请启动新流程；未覆盖人工修改')
        if any(node.get('status') == 'running' for node in output.get('nodes', {}).values()):
            output.update(unknown_calls=True, api_calls=None, usage_complete=False)
        run.input_scope['resume_count'] += 1
        run.input_scope['stage'] = 'queued'
        conn.execute("UPDATE runs SET status='pending',error=NULL,input_scope=?,output=? WHERE id=?",
                     (json.dumps(run.input_scope), json.dumps(output), str(run.id)))
    return get_run(settings, run.id)


class State(TypedDict):
    note_id: str | None


def execute_workflow(worker: ReadingWorker, run: Run):
    settings = worker.settings
    started = time.monotonic()
    output = run.output or {'mode': 'live', 'role': 'workflow', 'nodes': {}, 'api_calls': 0,
        'known_api_calls': 0, 'budget_used': 0, 'usage': None, 'usage_complete': True}
    attempt = run.input_scope['resume_count'] + 1
    active_role = None

    def persist(conn):
        conn.execute('UPDATE runs SET input_scope=?,output=? WHERE id=?',
                     (json.dumps(run.input_scope), json.dumps(output), str(run.id)))

    def node(role: str, state: State):
        nonlocal active_role
        active_role = role
        node_started = time.monotonic()
        with closing(db.connect(settings.db_path)) as conn:
            row = conn.execute('SELECT * FROM notes WHERE id=? AND document_id=?', (state['note_id'], str(run.document_id))).fetchone()
        base = note_from_row(row) if row else None
        if state['note_id'] and base is None:
            raise ValueError('节点输入笔记缺失或文件版本不匹配')
        combined = base.items + base.writing_items if base else []
        targets = [item for item in combined if item.verification_status != 'supported']
        evidence = (context(settings.db_path, run.document_id, [key for item in targets for key in item.evidence_ids]) if role == 'verification'
                    else retrieve(settings.db_path, run.document_id, run.input_scope['section'], run.input_scope['query']))
        if not evidence:
            raise ValueError('节点所选范围没有可读取原文')
        key = cache_key(settings, role, run.input_scope, base, evidence)
        previous = output['nodes'].get(role)
        if previous and previous['status'] == 'succeeded' and previous['cache_key'] != key:
            raise ValueError('已完成节点的输入或Prompt已变化，请启动新流程')
        worker.stage(run, role)
        with closing(db.connect(settings.db_path)) as conn:
            cached = conn.execute("SELECT run_id,output FROM run_steps WHERE role=? AND status='succeeded' AND json_extract(output,'$.cache_key')=? ORDER BY rowid DESC LIMIT 1", (role, key)).fetchone()
            if cached:
                saved = json.loads(cached['output'])
                exists = conn.execute('SELECT 1 FROM notes WHERE id=? AND document_id=?', (saved.get('note_id'), str(run.document_id))).fetchone()
                if not exists:
                    raise ValueError('缓存所引用的笔记缺失或文件版本不匹配')
                reused = {**saved, 'cached': True, 'api_calls': 0, 'usage': None, 'attempt': attempt,
                          'cache_source_run_id': cached['run_id']}
                output['nodes'][role] = reused
                if not output.get('note_id'):
                    output['note_id'] = saved['note_id']
                with db.transaction(settings.db_path) as transaction:
                    transaction.execute('''INSERT INTO run_steps(id,run_id,role,attempt,status,output,elapsed_ms)
                        VALUES (?,?,?,?,'succeeded',?,?) ON CONFLICT(run_id,role,attempt)
                        DO UPDATE SET status='succeeded',output=excluded.output,error=NULL,elapsed_ms=excluded.elapsed_ms''',
                        (str(uuid4()), str(run.id), role, attempt, json.dumps(reused), int((time.monotonic()-node_started)*1000)))
                    persist(transaction)
                return {'note_id': saved['note_id']}
        manual = bool(base and base.is_user_edited and (base.items if role == 'reading' else base.writing_items)) if role != 'verification' else False
        verification_allowance = 10 if run.input_scope['revision_limit'] and base and not base.is_user_edited else 4
        allowance = 0 if manual or role == 'verification' and not targets else verification_allowance if role == 'verification' else 2
        if output['budget_used'] + allowance > run.input_scope['max_api_calls']:
            raise ValueError('工作流调用预算已耗尽，未继续调用模型')
        output['budget_used'] += allowance
        info = {'status': 'running', 'cached': False, 'cache_key': key, 'attempt': attempt,
                'manual_preserved': manual, 'input_note_version': base.version if base else None,
                'physical_pages': sorted({block.physical_page for block in evidence}),
                'context_chars': sum(len(block.text) for block in evidence), 'error': None}
        output['nodes'][role] = info
        run.input_scope.update(physical_pages=info['physical_pages'], context_chars=info['context_chars'])
        with db.transaction(settings.db_path) as conn:
            if latest_id(conn, run.document_id) != output.get('note_id', run.input_scope['base_note_id']):
                raise ValueError('节点运行前笔记已有新版本，未覆盖人工修改')
            conn.execute('''INSERT INTO run_steps(id,run_id,role,attempt,status,output) VALUES (?,?,?,?,?,?)
                ON CONFLICT(run_id,role,attempt) DO UPDATE SET status='running',output=excluded.output,error=NULL''',
                         (str(uuid4()), str(run.id), role, attempt, 'running', json.dumps(info)))
            persist(conn)
        reply = None
        complete_usage = None
        phase_started = time.monotonic()
        try:
            if manual or role == 'verification' and not targets:
                result = base
                reply = Completion('', None, 0)
            elif role in {'reading', 'writing'}:
                reply = (reading.read(settings, evidence) if role == 'reading'
                         else writing.read(settings, evidence, base.items, run.input_scope['conditions']))
                items = (reading.validate_output(reply.content, evidence) if role == 'reading'
                         else writing.validate_output(reply.content, evidence))
                result = Note(paper_id=base.paper_id if base else document_paper(settings, run.document_id),
                    document_id=run.document_id, version=base.version+1 if base else 1,
                    parent_note_id=base.id if base else None, items=items if role == 'reading' else base.items,
                    writing_items=items if role == 'writing' else base.writing_items if base and base.is_user_edited else [],
                    is_user_edited=base.is_user_edited if base else False,
                    source_mode=base.source_mode if role == 'writing' else 'live')
            else:
                if len(combined) > 14 or sum(len(item.content) for item in combined) > 16000:
                    raise ValueError('核查条目超出14项/16000字符上限')
                scope = {**run.input_scope, 'role': 'verification', 'stage': 'queued', 'parent_run_id': str(run.id),
                         'base_note_id': str(base.id), 'revision_limit': int(bool(run.input_scope['revision_limit']) and not base.is_user_edited),
                         'revision_prompt_version': verification.REVISION_PROMPT_VERSION,
                         'verification_batch_size': 7, 'max_api_calls': verification_allowance}
                child = Run(document_id=run.document_id, model_name=settings.model_name,
                            prompt_version=verification.PROMPT_VERSION, parse_version=run.parse_version, input_scope=scope)
                info['child_run_id'] = str(child.id)
                with db.transaction(settings.db_path) as conn:
                    db.insert_run(conn, child, 'verification')
                    persist(conn)
                execute_verification(worker, child)
                child = get_run(settings, child.id)
                reply = Completion('', child.output.get('usage'), child.output.get('api_calls', 0))
                complete_usage = child.output.get('usage_complete', False)
                output.update(checks=child.output.get('checks', []), revision_rounds=child.output.get('revision_rounds', 0))
                if 'unresolved_item_ids' in child.output:
                    output['unresolved_item_ids'] = child.output['unresolved_item_ids']
                else:
                    output.pop('unresolved_item_ids', None)
                if child.status != 'succeeded':
                    raise ValueError('核查节点失败：' + (child.error or '原因未知'))
                with closing(db.connect(settings.db_path)) as conn:
                    result = note_from_row(conn.execute('SELECT * FROM notes WHERE id=?', (child.output['note_id'],)).fetchone())
            account(output, reply, usage_complete=complete_usage)
            info.update(status='succeeded', note_id=str(result.id), api_calls=reply.calls, usage=reply.usage, accounted=True)
            with db.transaction(settings.db_path) as conn:
                expected = str(result.id) if role == 'verification' and targets else output.get('note_id', run.input_scope['base_note_id'])
                if latest_id(conn, run.document_id) != expected:
                    raise ValueError('节点保存前笔记已有新版本，未覆盖人工修改')
                if role != 'verification' and not manual:
                    db.insert_note(conn, result)
                output['note_id'] = str(result.id)
                conn.execute("UPDATE run_steps SET status='succeeded',output=?,elapsed_ms=? WHERE run_id=? AND role=? AND attempt=?",
                             (json.dumps(info), int((time.monotonic()-phase_started)*1000), str(run.id), role, attempt))
                persist(conn)
            return {'note_id': str(result.id)}
        except Exception as exc:
            if not info.get('accounted'):
                account(output, reply or Completion('', None, getattr(exc, 'calls', 0)), usage_complete=complete_usage)
                info['accounted'] = True
            info.update(status='failed', error=safe_error(settings, exc))
            with db.transaction(settings.db_path) as conn:
                conn.execute("UPDATE run_steps SET status='failed',output=?,elapsed_ms=?,error=? WHERE run_id=? AND role=? AND attempt=?",
                             (json.dumps(info), int((time.monotonic()-phase_started)*1000), info['error'], str(run.id), role, attempt))
                persist(conn)
            raise

    try:
        graph = StateGraph(State)
        for role in ('reading', 'writing', 'verification'):
            graph.add_node(role, lambda state, role=role: node(role, state))
        graph.add_edge(START, 'reading')
        graph.add_edge('reading', 'writing')
        graph.add_edge('writing', 'verification')
        graph.add_edge('verification', END)
        graph.compile().invoke({'note_id': run.input_scope['base_note_id']}, {'recursion_limit': 5})
        run.input_scope['stage'] = 'saved'
        status, error = 'succeeded', None
    except Exception as exc:
        status, error = 'failed', safe_error(settings, exc)
        run.input_scope['stage'] = 'failed'
        if active_role and active_role not in output['nodes']:
            output['nodes'][active_role] = {'status': 'failed', 'cached': False, 'physical_pages': [],
                'context_chars': 0, 'input_note_version': None, 'error': error}
        elif active_role and output['nodes'][active_role]['status'] == 'running':
            output['nodes'][active_role].update(status='failed', error=error)
    with db.transaction(settings.db_path) as conn:
        if status == 'failed':
            conn.execute("UPDATE run_steps SET status='failed',error=? WHERE run_id=? AND status IN ('pending','running')", (error, str(run.id)))
        conn.execute('UPDATE runs SET status=?,input_scope=?,output=?,elapsed_ms=?,error=? WHERE id=?',
                     (status, json.dumps(run.input_scope), json.dumps(output), (run.elapsed_ms or 0)+int((time.monotonic()-started)*1000), error, str(run.id)))


def document_paper(settings: Settings, document_id: UUID) -> str:
    with closing(db.connect(settings.db_path)) as conn:
        row = conn.execute('SELECT paper_id FROM documents WHERE id=?', (str(document_id),)).fetchone()
    if row is None:
        raise ValueError('文档文件版本已缺失')
    return row['paper_id']
