"""One bounded proposal call with persistent cache and immutable source snapshots."""
from contextlib import closing
import hashlib
import json
import time

from app import db
from app.agents import ideas as agent
from app.config import PROJECT_ROOT
from app.services import ideas
from app.services.reading_runs import safe_error
from app.services.workflow import config_digest, digest


def prepare(settings, choices, conditions):
    facts, evidence, edits = ideas.inputs(settings, choices)
    key = digest({'role': 'idea', 'schema': 1, 'model': config_digest(settings), 'facts': facts, 'evidence': evidence,
        'conditions': conditions, 'idea_manual_revisions': edits, 'prompt_version': agent.PROMPT_VERSION,
        'prompt': hashlib.sha256((PROJECT_ROOT/f'prompts/{agent.PROMPT_VERSION}.md').read_bytes()).hexdigest()})
    return facts, evidence, key


def execute_idea(worker, run):
    settings = worker.settings
    started = time.monotonic()
    reply = None
    output = {'mode': 'live', 'role': 'idea', 'api_calls': 0, 'usage': None, 'cached': False, 'usage_complete': True}
    try:
        worker.stage(run, 'idea')
        choices = [agent.SourceChoice.model_validate(value) for value in run.input_scope['sources']]
        facts, evidence, key = prepare(settings, choices, run.input_scope['conditions'])
        output['cache_key'] = key
        run.input_scope.update(context_chars=sum(len(value['text']) for value in evidence),
            evidence_ids=[value['id'] for value in evidence], source_versions=[{
                name: value[name] for name in ('document_id','sha256','parse_version','note_id','note_version','item_id','verification_run_id')}
                for value in facts])
        with closing(db.connect(settings.db_path)) as conn:
            cached = conn.execute("SELECT output FROM run_steps WHERE role='idea' AND status='succeeded' AND json_extract(output,'$.cache_key')=? ORDER BY rowid DESC LIMIT 1", (key,)).fetchone()
            if cached:
                previous = json.loads(cached['output'])
                row = conn.execute('SELECT * FROM ideas WHERE id=?', (previous['idea_id'],)).fetchone()
                if row is None:
                    raise ValueError('Idea缓存所引用的版本缺失')
                idea = ideas.from_row(row)
                head = conn.execute('SELECT id FROM ideas WHERE root_id=? ORDER BY version DESC LIMIT 1', (str(idea.root_id),)).fetchone()
                if head['id'] != str(idea.id):
                    raise ValueError('Idea缓存版本已有修订，请刷新来源后重新生成')
                output.update(idea_id=str(idea.id), cached=True)
        if not cached:
            reply = agent.generate(settings, facts, evidence, run.input_scope['conditions'])
            output.update(api_calls=reply.calls, usage=reply.usage, usage_complete=bool(reply.usage) and reply.calls == 1)
            draft = agent.IdeaDraft.model_validate_json(reply.content)
            with db.transaction(settings.db_path) as conn:
                idea = ideas.insert(conn, draft, facts, run_id=run.id)
                output['idea_id'] = str(idea.id)
                run.input_scope['stage'] = 'saved'
                finish(conn, run, output, started)
            return
        run.input_scope['stage'] = 'saved'
        with db.transaction(settings.db_path) as conn:
            finish(conn, run, output, started)
    except Exception as exc:
        if reply is None:
            output.update(api_calls=getattr(exc, 'calls', 0), usage_complete=False)
        error = safe_error(settings, exc)
        run.input_scope['stage'] = 'failed'
        with db.transaction(settings.db_path) as conn:
            finish(conn, run, output, started, error)


def finish(conn, run, output, started, error=None):
    status = 'failed' if error else 'succeeded'
    elapsed = int((time.monotonic()-started)*1000)
    conn.execute('UPDATE runs SET status=?,input_scope=?,output=?,elapsed_ms=?,error=? WHERE id=?',
        (status,json.dumps(run.input_scope),json.dumps(output),elapsed,error,str(run.id)))
    conn.execute("UPDATE run_steps SET status=?,output=?,elapsed_ms=?,error=? WHERE run_id=? AND role='idea' AND attempt=1",
        (status,json.dumps(output),elapsed,error,str(run.id)))
