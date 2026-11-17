"""One local worker; persisted states and append-only, optimistic note versions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import time
from uuid import UUID, uuid4

from pydantic import ValidationError

from app import db
from app.agents import reading, verification
from app.agents.client import MAX_CALLS, TIMEOUT_SECONDS, ModelError
from app.config import Settings
from app.schemas import AnalysisItem, Note, Run
from app.services.retrieval import MAX_BLOCKS, MAX_CHARS, retrieve


def note_from_row(row) -> Note:
    values = dict(row)
    values['items'] = json.loads(values.pop('items_json'))
    values['writing_items'] = json.loads(values.pop('writing_items_json'))
    return Note.model_validate(values)


def list_notes(settings: Settings, document_id: UUID) -> list[Note]:
    with closing(db.connect(settings.db_path)) as conn:
        return [note_from_row(row) for row in conn.execute(
            'SELECT * FROM notes WHERE document_id=? ORDER BY version DESC', (str(document_id),))]


def run_from_row(row) -> Run:
    values = dict(row)
    for key in ('input_scope', 'output'):
        values[key] = json.loads(values[key]) if values[key] is not None else None
    return Run.model_validate(values)


def get_run(settings: Settings, run_id: UUID) -> Run:
    with closing(db.connect(settings.db_path)) as conn:
        row = conn.execute('SELECT * FROM runs WHERE id=?', (str(run_id),)).fetchone()
    if row is None:
        raise LookupError('精读任务不存在')
    return run_from_row(row)


def safe_error(settings: Settings, exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        message = '模型结构不合规：条目或核查结果未满足结构约束'
    elif isinstance(exc, (ModelError, ValueError)):
        message = str(exc)
    else:
        message = f'分析处理失败（{type(exc).__name__}）'
    return message.replace(settings.model_api_key, '[已隐藏]') if settings.model_api_key else message


def save_manual(settings: Settings, document_id: UUID, base_id: UUID, changes: dict[UUID, str]) -> Note:
    with db.transaction(settings.db_path) as conn:
        row = conn.execute('SELECT * FROM notes WHERE document_id=? ORDER BY version DESC LIMIT 1', (str(document_id),)).fetchone()
        if row is None or row['id'] != str(base_id):
            raise ValueError('笔记已有新版本，请先查看新版本；未覆盖当前编辑')
        base = note_from_row(row)
        if not changes or set(changes) - {item.id for item in base.items}:
            raise ValueError('修改内容为空或含未知条目ID')
        items = []
        for item in base.items:
            values = item.model_dump()
            if item.id in changes:
                text = changes[item.id].strip()
                if not text or len(text) > 10000:
                    raise ValueError('人工笔记须为1—10000字')
                values.update(content=text, verification_status='unchecked', verification_reason='人工修订，待原文核查')
            items.append(AnalysisItem.model_validate(values))
        return db.insert_note(conn, Note(paper_id=base.paper_id, document_id=document_id, version=base.version+1,
                                        parent_note_id=base.id, items=items, writing_items=base.writing_items,
                                        is_user_edited=True, source_mode=base.source_mode,
                                        verification_run_id=base.verification_run_id))


class ReadingWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='papertrail-reading')
        with db.transaction(settings.db_path) as conn:
            conn.execute("UPDATE runs SET status='failed',error='服务重启中断，请重新运行' WHERE status IN ('pending','running') AND id IN (SELECT run_id FROM run_steps WHERE role IN ('reading','verification'))")
            conn.execute("UPDATE run_steps SET status='failed',error='服务重启中断，请重新运行' WHERE role IN ('reading','verification') AND status IN ('pending','running')")

    def close(self):
        self.executor.shutdown(wait=True)

    def start(self, document_id: UUID, mode: str, section: str, query: str,
              base_note_id: UUID | None = None, revise: bool = True) -> Run:
        if mode == 'live':
            missing, errors = self.settings.model_status()
            if missing or errors:
                raise ModelError('模型配置缺失或无效：' + '、'.join(missing + errors))
        with db.transaction(self.settings.db_path) as conn:
            document = conn.execute('SELECT * FROM documents WHERE id=?', (str(document_id),)).fetchone()
            if document is None:
                raise LookupError('文档不存在')
            if document['parse_status'] != 'parsed':
                raise ValueError('文档尚未成功提取文字，无法精读')
            active = conn.execute("SELECT document_id FROM runs WHERE status IN ('pending','running')").fetchall()
            if len(active) >= 10 or any(row['document_id'] == str(document_id) for row in active):
                raise ValueError('该文档已有活动任务或队列已满（最多10项）')
            role = 'verification' if base_note_id else 'reading'
            if base_note_id:
                row = conn.execute('SELECT * FROM notes WHERE document_id=? ORDER BY version DESC LIMIT 1', (str(document_id),)).fetchone()
                if row is None or row['id'] != str(base_note_id):
                    raise ValueError('只能核查该文件的最新笔记版本，请先查看新版本')
                base = note_from_row(row)
                if not 1 <= len(base.items) <= 9 or sum(len(item.content) for item in base.items) > MAX_CHARS:
                    raise ValueError('核查限1—9个条目，条目正文总长最多16000字符')
                if all(item.verification_status == 'supported' for item in base.items):
                    raise ValueError('该版本条目已全部支持，无需重复核查')
                revise = revise and not base.is_user_edited
            scope = {'mode': mode, 'role': role, 'section': section, 'query': query, 'stage': 'queued',
                     'sha256': document['sha256'], 'parse_version': document['parse_version'],
                     'max_context_chars': MAX_CHARS, 'max_blocks': MAX_BLOCKS,
                     'max_api_calls': MAX_CALLS, 'timeout_seconds': TIMEOUT_SECONDS}
            if base_note_id:
                scope.update(base_note_id=str(base_note_id), revision_limit=int(revise),
                             revision_prompt_version=verification.REVISION_PROMPT_VERSION,
                             max_api_calls=MAX_CALLS * (3 if revise else 1))
            run = Run(document_id=document_id, model_name=self.settings.model_name if mode == 'live' else 'fixed-replay',
                      prompt_version=verification.PROMPT_VERSION if base_note_id else reading.PROMPT_VERSION,
                      parse_version=document['parse_version'], input_scope=scope)
            conn.execute('''INSERT INTO runs
                (id,document_id,status,model_name,prompt_version,parse_version,input_scope,created_at)
                VALUES (?,?,?,?,?,?,?,?)''', (str(run.id), str(document_id), run.status, run.model_name,
                run.prompt_version, run.parse_version, json.dumps(scope), run.created_at.isoformat()))
            conn.execute('INSERT INTO run_steps(id,run_id,role,attempt,status) VALUES (?,?,?,?,?)',
                         (str(uuid4()), str(run.id), role, 1, 'pending'))
        self.executor.submit(self.execute, run.id)
        return run

    def stage(self, run: Run, stage: str):
        run.input_scope['stage'] = stage
        with db.transaction(self.settings.db_path) as conn:
            conn.execute("UPDATE runs SET status='running',input_scope=? WHERE id=?", (json.dumps(run.input_scope), str(run.id)))
            conn.execute("UPDATE run_steps SET status='running' WHERE run_id=? AND status='pending'", (str(run.id),))

    def execute(self, run_id: UUID):
        run = get_run(self.settings, run_id)
        started = time.monotonic()
        reply = None
        try:
            if run.input_scope.get('role') == 'verification':
                from app.services.verification_runs import execute_verification
                return execute_verification(self, run)
            self.stage(run, 'retrieving')
            evidence = retrieve(self.settings.db_path, run.document_id, str(run.input_scope['section']), str(run.input_scope['query']))
            if not evidence:
                raise ValueError('所选章节/关键词范围没有可读取证据')
            run.input_scope.update(evidence_ids=[str(block.id) for block in evidence],
                                   physical_pages=sorted({block.physical_page for block in evidence}),
                                   context_chars=sum(len(block.text) for block in evidence),
                                   coverage='仅分析本次读取的文字，不保证覆盖全文或图表')
            self.stage(run, 'calling_model' if run.input_scope['mode'] == 'live' else 'replaying')
            reply = reading.read(self.settings, evidence) if run.input_scope['mode'] == 'live' else reading.replay(str(run.input_scope['sha256']), evidence)
            self.stage(run, 'validating')
            items = reading.validate_output(reply.content, evidence)
            with db.transaction(self.settings.db_path) as conn:
                document = conn.execute('SELECT paper_id FROM documents WHERE id=?', (str(run.document_id),)).fetchone()
                parent = conn.execute('SELECT * FROM notes WHERE document_id=? ORDER BY version DESC LIMIT 1', (str(run.document_id),)).fetchone()
                note = Note(paper_id=document['paper_id'], document_id=run.document_id, items=items,
                            version=parent['version']+1 if parent else 1, parent_note_id=parent['id'] if parent else None,
                            writing_items=note_from_row(parent).writing_items if parent else [],
                            source_mode=run.input_scope['mode'])
                db.insert_note(conn, note)
                output = {'note_id': str(note.id), 'mode': run.input_scope['mode'], 'usage': reply.usage,
                          'api_calls': reply.calls, 'semantic_verification': False}
                run.input_scope['stage'] = 'saved'
                elapsed = int((time.monotonic()-started)*1000)
                conn.execute("UPDATE runs SET status='succeeded',input_scope=?,output=?,elapsed_ms=? WHERE id=?",
                             (json.dumps(run.input_scope), json.dumps(output), elapsed, str(run.id)))
                conn.execute("UPDATE run_steps SET status='succeeded',output=?,elapsed_ms=? WHERE run_id=? AND role='reading'",
                             (json.dumps(output), elapsed, str(run.id)))
        except Exception as exc:
            message = safe_error(self.settings, exc)
            output = {'mode': run.input_scope['mode'], 'usage': reply.usage if reply else None,
                      'api_calls': reply.calls if reply else getattr(exc, 'calls', 0)}
            run.input_scope['stage'] = 'failed'
            elapsed = int((time.monotonic()-started)*1000)
            with db.transaction(self.settings.db_path) as conn:
                conn.execute("UPDATE runs SET status='failed',input_scope=?,output=?,elapsed_ms=?,error=? WHERE id=?",
                             (json.dumps(run.input_scope), json.dumps(output), elapsed, message, str(run.id)))
                conn.execute("UPDATE run_steps SET status='failed',output=?,elapsed_ms=?,error=? WHERE run_id=? AND status IN ('pending','running')",
                             (json.dumps(output), elapsed, message, str(run.id)))
