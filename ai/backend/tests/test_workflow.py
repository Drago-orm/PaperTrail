"""Controlled responses exercise durable workflow boundaries, not model quality."""
from dataclasses import replace
import json
from threading import Event
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app import db
from app.agents import reading, verification, writing
from app.agents.client import Completion, ModelError
from app.config import Settings
from app.main import create_app
from app.schemas import Note
from app.services import workflow
from app.services.reading_runs import list_notes
from app.services.retrieval import retrieve
from test_reading import controlled_output, upload, wait_run


def writing_output(user):
    source = json.loads(user)['paper_content'][-1]
    return {'items': [{'field': field, 'content_type': 'system_analysis',
        'content': '原段落给出具体证据；可迁移策略：用明确例子说明方法，适用范围需结合本次原文。',
        'verification_status': 'unchecked', 'evidence_ids': [source['id']],
        'quotes': [{'evidence_id': source['id'], 'text': source['text']}]} for field in writing.FIELDS]}


def verifier_output(user):
    payload = json.loads(user)
    source = payload['paper_content'][-1]
    return {'items': [{'item_id': item['id'], 'status': 'not_reported' if item['content'] == '未报告' else 'supported',
        'reason': '受控测试核查，非真实模型准确率。', 'evidence_ids': [source['id']],
        'quotes': [{'evidence_id': source['id'], 'text': source['text']}]} for item in payload['claims']]}


def configure(tmp_path):
    return Settings('https://controlled.invalid/v1', 'controlled', 'never-log-secret', data_dir=tmp_path)


def transports(monkeypatch):
    calls = {'reading': 0, 'writing': 0, 'verification': 0}
    for role, module, response in [('reading', reading, controlled_output), ('writing', writing, writing_output),
                                   ('verification', verification, verifier_output)]:
        def controlled(settings, system, user, role=role, response=response):
            calls[role] += 1
            return Completion(json.dumps(response(user)), {'total_tokens': 10}, 1)
        monkeypatch.setattr(module, 'complete', controlled)
    return calls


def start(client, did, **changes):
    response = client.post(f'/api/documents/{did}/workflow-runs', json={'revise': False, 'include_idea':False, **changes})
    assert response.status_code == 202, response.text
    return response.json()


def test_writing_failure_keeps_reading_and_restart_reuses_success(tmp_path, monkeypatch):
    settings = configure(tmp_path)
    calls = transports(monkeypatch)
    original = writing.complete
    def fail(settings, system, user):
        calls['writing'] += 1
        raise ModelError('受控写作失败', calls=2)
    monkeypatch.setattr(writing, 'complete', fail)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        first = wait_run(client, start(client, did, interest='  methods  ', resources='CPU')['id'])
        assert first['status'] == 'failed' and first['output']['api_calls'] == 3
        saved_reading = list_notes(settings, did)[0]
        assert len(saved_reading.items) == 9 and not saved_reading.writing_items
    monkeypatch.setattr(writing, 'complete', original)
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/workflow-runs/'+first['id']+'/retry')
        assert response.status_code == 202, response.text
        result = wait_run(client, first['id'])
        assert result['status'] == 'succeeded', result
        assert calls == {'reading': 1, 'writing': 2, 'verification': 2}
        assert result['output']['nodes']['reading']['cached']
        assert result['output']['api_calls'] == 6 and result['output']['budget_used'] <= 24
        assert result['input_scope']['conditions']['interest'] == 'methods'
        notes = list_notes(settings, did)
        assert len(notes) == 3 and notes[-1] == saved_reading
        assert len(notes[0].writing_items) == 5 and all(item.verification_status == 'supported' for item in notes[0].writing_items)
        assert sum(len(check['items']) for check in result['output']['checks']) == 14
        assert client.get(f'/api/documents/{did}/reading-runs').json()[0]['id'] == first['id']


def test_full_budget_and_partial_verification_findings_survive_failed_recheck(tmp_path, monkeypatch):
    settings = configure(tmp_path)
    calls = transports(monkeypatch)
    phases = []
    def controlled(settings, system, user):
        payload = json.loads(user)
        phases.append(payload)
        if 'issues' in payload:
            result = {'items': payload['claims']}
            for item in result['items']:
                item.update(verification_status='unchecked', verification_reason='')
        elif len(phases) == 4:
            raise ModelError('受控复查失败', calls=2)
        else:
            result = verifier_output(user)
            if len(phases) in {1, 2, 5, 6}:
                target = next(item for item in result['items'] if item['status'] == 'supported')
                target['status'] = 'partially_supported'
        return Completion(json.dumps(result), {'total_tokens': 10}, 2)
    monkeypatch.setattr(verification, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        first = wait_run(client, start(client, did, revise=True)['id'])
        assert first['status'] == 'failed' and len(first['output']['checks']) == 2
        assert 'unresolved_item_ids' not in first['output']
        assert len(list_notes(settings, did)) == 2
        assert client.post('/api/workflow-runs/'+first['id']+'/retry').status_code == 202
        final = wait_run(client, first['id'])
        assert final['status'] == 'succeeded', final
        assert final['output']['budget_used'] == 24 and final['output']['api_calls'] == 18
        assert len(phases) == 8 and calls['reading'] == calls['writing'] == 1
        assert client.post('/api/workflow-runs/'+first['id']+'/retry').status_code == 409


def test_interruption_has_unknown_calls_and_recovers_checkpoint(tmp_path, monkeypatch):
    settings = configure(tmp_path)
    calls = transports(monkeypatch)
    original = writing.complete
    def interrupt(*args):
        raise SystemExit('controlled process interruption')
    monkeypatch.setattr(writing, 'complete', interrupt)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        queued = start(client, did)
    monkeypatch.setattr(writing, 'complete', original)
    with TestClient(create_app(settings)) as client:
        interrupted = client.get('/api/reading-runs/'+queued['id']).json()
        assert interrupted['status'] == 'failed' and '重启中断' in interrupted['error']
        assert client.post('/api/workflow-runs/'+queued['id']+'/retry').status_code == 202
        result = wait_run(client, queued['id'])
        assert result['status'] == 'succeeded', result
        assert result['output']['api_calls'] is None and result['output']['unknown_calls']
        assert result['output']['known_api_calls'] == 4 and calls['reading'] == 1
        assert len(list_notes(settings, did)) == 3


def test_child_commit_survives_parent_interruption_without_duplicate_notes(tmp_path, monkeypatch):
    settings = configure(tmp_path)
    calls = transports(monkeypatch)
    original = workflow.account
    def interrupt(output, reply, **kwargs):
        if reply.content == '' and reply.calls:
            raise SystemExit('controlled stop after child commit')
        return original(output, reply, **kwargs)
    monkeypatch.setattr(workflow, 'account', interrupt)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        queued = start(client, did)
    monkeypatch.setattr(workflow, 'account', original)
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/workflow-runs/'+queued['id']+'/retry').status_code == 202
        result = wait_run(client, queued['id'])
        assert result['status'] == 'succeeded', result
        assert calls == {'reading': 1, 'writing': 1, 'verification': 2}
        assert len(list_notes(settings, did)) == 3
        assert result['output']['api_calls'] == 4


@pytest.mark.parametrize('writing_only', [False, True])
def test_manual_writing_changes_are_preserved_and_invalidate_downstream(tmp_path, monkeypatch, writing_only):
    settings = configure(tmp_path)
    calls = transports(monkeypatch)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        assert wait_run(client, start(client, did)['id'])['status'] == 'succeeded'
        base = list_notes(settings, did)[0]
        edited = client.post(f'/api/documents/{did}/notes', json={'base_note_id': str(base.id),
            'changes': {str(base.writing_items[0].id): 'Human writing strategy'}})
        assert edited.status_code == 201
        assert edited.json()['writing_items'][0]['verification_status'] == 'unchecked'
        if writing_only:
            manual = list_notes(settings, did)[0]
            db.create_note(settings.db_path, Note(paper_id=manual.paper_id, document_id=manual.document_id,
                parent_note_id=manual.id, version=manual.version+1, is_user_edited=True,
                writing_items=manual.writing_items, source_mode=manual.source_mode))
        rerun = wait_run(client, start(client, did)['id'])
        assert rerun['status'] == 'succeeded', rerun
        assert calls == {'reading': 2 if writing_only else 1, 'writing': 1, 'verification': 4}
        assert rerun['output']['nodes']['reading']['manual_preserved'] is (not writing_only)
        assert rerun['output']['nodes']['writing']['manual_preserved']
        assert list_notes(settings, did)[0].writing_items[0].content == 'Human writing strategy'
        assert any(note.model_dump(mode='json') == edited.json() for note in list_notes(settings, did))


def test_retry_limit_and_changed_configuration_are_explicit(tmp_path, monkeypatch):
    settings = configure(tmp_path)
    calls = transports(monkeypatch)
    def fail(*args):
        calls['writing'] += 1
        raise ModelError('受控失败', calls=2)
    monkeypatch.setattr(writing, 'complete', fail)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        first = wait_run(client, start(client, did)['id'])
        assert client.post('/api/workflow-runs/'+first['id']+'/retry').status_code == 202
        assert wait_run(client, first['id'])['status'] == 'failed'
        assert client.post('/api/workflow-runs/'+first['id']+'/retry').status_code == 409
        assert calls['reading'] == 1 and calls['writing'] == 2
        other = wait_run(client, start(client, did)['id'])
    with TestClient(create_app(replace(settings, model_name='changed'))) as client:
        response = client.post('/api/workflow-runs/'+other['id']+'/retry')
        assert response.status_code == 409 and '模型配置已变化' in response.text


def test_concurrent_human_version_stops_node_and_stale_retry(tmp_path, monkeypatch):
    settings = configure(tmp_path)
    transports(monkeypatch)
    entered, release = Event(), Event()
    def blocked(settings, system, user):
        entered.set()
        assert release.wait(4)
        return Completion(json.dumps(writing_output(user)), None, 1)
    monkeypatch.setattr(writing, 'complete', blocked)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        queued = start(client, did)
        try:
            assert entered.wait(4)
            base = list_notes(settings, did)[0]
            saved = client.post(f'/api/documents/{did}/notes', json={'base_note_id': str(base.id),
                'changes': {str(base.items[0].id): 'Concurrent human content'}})
            assert saved.status_code == 201
        finally:
            release.set()
        result = wait_run(client, queued['id'])
        assert result['status'] == 'failed' and '未覆盖人工修改' in result['error']
        assert list_notes(settings, did)[0].model_dump(mode='json') == saved.json()
        assert client.post('/api/workflow-runs/'+queued['id']+'/retry').status_code == 409


def test_cache_key_covers_versions_conditions_prompts_configuration_and_evidence(tmp_path, monkeypatch):
    settings = configure(tmp_path)
    transports(monkeypatch)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        done = wait_run(client, start(client, did)['id'])
        base = list_notes(settings, did)[-1]
    evidence = retrieve(settings.db_path, base.document_id)
    scope = done['input_scope']
    key = workflow.cache_key(settings, 'writing', scope, base, evidence)
    assert key == workflow.cache_key(settings, 'writing', dict(reversed(list(scope.items()))), base, evidence)
    for change in [replace(settings, model_name='other'), replace(settings, model_base_url='https://other.invalid/v1'), replace(settings, model_api_key='other-secret')]:
        assert key != workflow.cache_key(change, 'writing', scope, base, evidence)
    for changes in [{'sha256': 'a'*64}, {'parse_version': 'changed'}, {'query': 'changed'},
                    {'conditions': {'interest': 'new', 'resources': ''}}, {'conditions': {'interest': '', 'resources': 'CPU'}}, {'revision_limit': 1}]:
        assert key != workflow.cache_key(settings, 'writing', {**scope, **changes}, base, evidence)
    changed = base.model_copy(deep=True)
    changed.items[0].content = 'Human edit'
    assert key != workflow.cache_key(settings, 'writing', scope, changed, evidence)
    changed = base.model_copy(update={'verification_run_id': UUID(done['id'])})
    assert key != workflow.cache_key(settings, 'writing', scope, changed, evidence)
    changed_source = evidence[0].model_copy(update={'text': 'Different original'})
    assert key != workflow.cache_key(settings, 'writing', scope, base, [changed_source, *evidence[1:]])
    prompt_root = tmp_path/'prompt-root'
    (prompt_root/'prompts').mkdir(parents=True)
    target = prompt_root/'prompts/writing-v1.md'
    target.write_text('changed prompt', encoding='utf-8')
    monkeypatch.setattr(workflow, 'PROJECT_ROOT', prompt_root)
    assert key != workflow.cache_key(settings, 'writing', scope, base, evidence)


@pytest.mark.parametrize('defect', ['field', 'quote'])
def test_bad_writing_is_rejected_without_losing_reading(tmp_path, monkeypatch, defect):
    settings = configure(tmp_path)
    transports(monkeypatch)
    def bad(settings, system, user):
        result = writing_output(user)
        if defect == 'field':
            result['items'][1]['field'] = result['items'][0]['field']
        else:
            result['items'][0]['quotes'][0]['text'] = 'Invented quotation'
        return Completion(json.dumps(result), None, 1)
    monkeypatch.setattr(writing, 'complete', bad)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        result = wait_run(client, start(client, did)['id'])
        assert result['status'] == 'failed' and result['output']['nodes']['writing']['status'] == 'failed'
        assert len(list_notes(settings, did)) == 1 and not list_notes(settings, did)[0].writing_items
