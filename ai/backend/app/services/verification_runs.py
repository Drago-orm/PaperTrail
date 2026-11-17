"""Fixed verify -> optional targeted revision -> recheck; never recurse or overwrite a base note."""
from contextlib import closing
import json
import time
from uuid import uuid4

from app import db
from app.agents import verification
from app.schemas import Note, Run
from app.services.reading_runs import ReadingWorker, note_from_row, safe_error
from app.services.retrieval import context


def execute_verification(worker: ReadingWorker, run: Run):
    settings = worker.settings
    started = time.monotonic()
    output = {'mode': 'live', 'role': 'verification', 'api_calls': 0, 'usage': None,
              'usage_complete': True, 'revision_rounds': 0, 'checks': []}

    def persist():
        with db.transaction(settings.db_path) as conn:
            conn.execute('UPDATE runs SET input_scope=?,output=? WHERE id=?',
                         (json.dumps(run.input_scope), json.dumps(output), str(run.id)))

    def reread(items, stage):
        worker.stage(run, stage)
        evidence = context(settings.db_path, run.document_id, [key for item in items for key in item.evidence_ids])
        if not evidence:
            raise ValueError('原文证据为空，无法进行语义核查')
        scope = {'stage': stage, 'evidence_ids': [str(block.id) for block in evidence],
                 'physical_pages': sorted({block.physical_page for block in evidence}),
                 'context_chars': sum(len(block.text) for block in evidence)}
        run.input_scope.update(scope, coverage='重新读取数据库原文及相邻上下文；仅限本次文字范围，不保证全文/图表覆盖')
        output.setdefault('contexts', []).append(scope)
        persist()
        return evidence

    def perform(items, evidence, role, attempt, issues=None):
        phase_started = time.monotonic()
        reply = None
        phase_output = None
        phase_error = None
        with db.transaction(settings.db_path) as conn:
            conn.execute('''INSERT INTO run_steps(id,run_id,role,attempt,status) VALUES (?,?,?,?,?)
                ON CONFLICT(run_id,role,attempt) DO UPDATE SET status='running' ''',
                         (str(uuid4()), str(run.id), role, attempt, 'running'))
        worker.stage(run, 'revising' if issues is not None else ('rechecking' if attempt == 2 else 'verifying'))
        try:
            reply = verification.request(settings, items, evidence, issues=issues)
            output['api_calls'] += reply.calls
            if reply.usage:
                usage = output['usage'] or {}
                for key, value in reply.usage.items():
                    usage[key] = usage.get(key, 0) + value
                output['usage'] = usage
            if not reply.usage or reply.calls != 1:
                output['usage_complete'] = False
            parsed = (verification.validate_revision(reply.content, items, evidence) if issues is not None
                      else verification.validate(reply.content, items, evidence))
            phase_output = {'items': [item.model_dump(mode='json') for item in parsed],
                            'api_calls': reply.calls, 'usage': reply.usage}
            if issues is None:
                output['checks'].append({'attempt': attempt, 'items': phase_output['items']})
            persist()
            return parsed
        except Exception as exc:
            if reply is None:
                output['api_calls'] += getattr(exc, 'calls', 0)
                output['usage_complete'] = False
            phase_error = safe_error(settings, exc)
            phase_output = {'api_calls': reply.calls if reply else getattr(exc, 'calls', 0),
                            'usage': reply.usage if reply else None}
            raise
        finally:
            with db.transaction(settings.db_path) as conn:
                conn.execute('UPDATE run_steps SET status=?,output=?,elapsed_ms=?,error=? WHERE run_id=? AND role=? AND attempt=?',
                             ('failed' if phase_error else 'succeeded', json.dumps(phase_output),
                              int((time.monotonic()-phase_started)*1000), phase_error, str(run.id), role, attempt))

    try:
        with closing(db.connect(settings.db_path)) as conn:
            row = conn.execute('SELECT * FROM notes WHERE id=? AND document_id=?',
                               (str(run.input_scope['base_note_id']), str(run.document_id))).fetchone()
        if row is None:
            raise ValueError('核查基版笔记不存在')
        base = note_from_row(row)
        targets = [item for item in base.items if item.verification_status != 'supported']
        evidence = reread(targets, 'rereading')
        verdicts = perform(targets, evidence, 'verification', 1)
        items = verification.apply(base.items, verdicts)
        problems = [item for item in items if item.verification_status != 'supported'
                    and not (item.verification_status == 'not_reported' and item.content == '未报告')]
        if problems and run.input_scope['revision_limit']:
            output['revision_rounds'] = 1
            problem_ids = {item.id for item in problems}
            issues = [verdict for verdict in verdicts if verdict.item_id in problem_ids]
            revised = perform(problems, evidence, 'reading', 1, issues=issues)
            evidence = reread(revised, 'rereading_revision')
            rechecked = perform(revised, evidence, 'verification', 2)
            replacements = {item.id: item for item in verification.apply(revised, rechecked)}
            items = [replacements.get(item.id, item) for item in items]
        output['unresolved_item_ids'] = [str(item.id) for item in items if item.verification_status != 'supported'
                                         and not (item.verification_status == 'not_reported' and item.content == '未报告')]
        output['not_reported_count'] = sum(item.verification_status == 'not_reported' for item in items)
        with db.transaction(settings.db_path) as conn:
            latest = conn.execute('SELECT id FROM notes WHERE document_id=? ORDER BY version DESC LIMIT 1',
                                  (str(run.document_id),)).fetchone()
            if latest is None or latest['id'] != str(base.id):
                raise ValueError('核查期间笔记已有新版本；核查记录已保留，未覆盖人工修改')
            note = Note(paper_id=base.paper_id, document_id=base.document_id, version=base.version+1,
                        parent_note_id=base.id, items=items, writing_items=base.writing_items,
                        is_user_edited=base.is_user_edited, source_mode=base.source_mode, verification_run_id=run.id)
            db.insert_note(conn, note)
            output['note_id'] = str(note.id)
            run.input_scope['stage'] = 'saved'
            conn.execute("UPDATE runs SET status='succeeded',input_scope=?,output=?,elapsed_ms=? WHERE id=?",
                         (json.dumps(run.input_scope), json.dumps(output), int((time.monotonic()-started)*1000), str(run.id)))
    except Exception as exc:
        error = safe_error(settings, exc)
        run.input_scope['stage'] = 'failed'
        with db.transaction(settings.db_path) as conn:
            conn.execute("UPDATE runs SET status='failed',input_scope=?,output=?,elapsed_ms=?,error=? WHERE id=?",
                         (json.dumps(run.input_scope), json.dumps(output), int((time.monotonic()-started)*1000), error, str(run.id)))
            conn.execute("UPDATE run_steps SET status='failed',error=? WHERE run_id=? AND status IN ('pending','running')",
                         (error, str(run.id)))
