"""Controlled model responses test boundaries, not real model quality or cost."""
from dataclasses import replace
from io import BytesIO
import json
import time
from uuid import UUID, uuid4
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from app.agents import client as model_client, reading
from app import db
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.services.retrieval import retrieve
from app.schemas import Paper
from test_db import add_document
from test_pdf_import import make_pdf


def upload(client, content=None):
    content = content if content is not None else (PROJECT_ROOT/'fixtures/reader.pdf').read_bytes()
    response = client.post('/api/documents', files={'file': ('fixture.pdf', content)})
    assert response.status_code == 200
    return response.json()['document']['id']


def wait_run(client, run_id):
    for _ in range(300):
        run = client.get('/api/reading-runs/'+run_id).json()
        if run['status'] in {'succeeded', 'failed'}:
            return run
        time.sleep(.01)
    pytest.fail('worker did not reach terminal state')


def controlled_output(user):
    source = json.loads(user)['paper_content'][-1]
    items = [{'field': field, 'content_type': 'system_analysis', 'content': '未报告',
              'verification_status': 'not_reported', 'evidence_ids': [], 'quotes': []} for field in reading.FIELDS]
    items[2].update(content='受控测试分析', verification_status='unchecked', evidence_ids=[source['id']],
                    quotes=[{'evidence_id': source['id'], 'text': source['text']}])
    return {'items': items}


def test_replay_versions_manual_preservation_conflict_and_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        assert client.post(f'/api/documents/{did}/reading-runs', json={}).status_code == 503
        response = client.post(f'/api/documents/{did}/reading-runs', json={'mode': 'replay'})
        assert response.status_code == 202
        run = wait_run(client, response.json()['id'])
        assert run['status'] == 'succeeded', run
        assert run['output']['api_calls'] == 0 and run['output']['usage'] is None
        assert run['input_scope']['mode'] == 'replay'
        notes = client.get(f'/api/documents/{did}/notes').json()
        original = notes[0]
        assert original['source_mode'] == 'replay'
        assert len(original['items']) == 9 and original['items'][2]['quotes']
        change = {original['items'][2]['id']: '人工修订，不可覆盖'}
        saved = client.post(f'/api/documents/{did}/notes', json={'base_note_id': original['id'], 'changes': change})
        assert saved.status_code == 201, saved.text
        manual = saved.json()
        assert manual['version'] == 2 and manual['is_user_edited']
        assert manual['source_mode'] == 'replay'
        assert manual['items'][0]['verification_status'] == 'not_reported'
        assert manual['items'][2]['id'] == original['items'][2]['id']
        assert client.post(f'/api/documents/{did}/notes', json={'base_note_id': original['id'], 'changes': change}).status_code == 409
        rerun = client.post(f'/api/documents/{did}/reading-runs', json={'mode': 'replay'}).json()
        assert wait_run(client, rerun['id'])['status'] == 'succeeded'
        notes = client.get(f'/api/documents/{did}/notes').json()
        assert [note['version'] for note in notes] == [3, 2, 1]
        assert notes[1]['items'][2]['content'] == '人工修订，不可覆盖'
    with TestClient(create_app(settings)) as client:
        assert client.get(f'/api/documents/{did}/notes').json()[1] == manual
        assert client.get(f'/api/documents/{did}/reading-runs').json()[0]['status'] == 'succeeded'


def test_replay_rejects_other_papers_and_empty_scope(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        did = upload(client, make_pdf([[(40,750,'Other paper content')]]))
        run = client.post(f'/api/documents/{did}/reading-runs', json={'mode': 'replay'}).json()
        assert '仅支持' in wait_run(client, run['id'])['error']
        run = client.post(f'/api/documents/{did}/reading-runs', json={'mode': 'replay', 'query': 'missing-scope'}).json()
        assert '没有可读取证据' in wait_run(client, run['id'])['error']


@pytest.mark.parametrize('defect', ['shape', 'id', 'quote', 'pretend_verified'])
def test_live_bad_outputs_fail_without_creating_notes(tmp_path, monkeypatch, defect):
    settings = Settings('https://model.invalid/v1', 'controlled', 'never-log-secret', data_dir=tmp_path)
    def controlled(settings, system, user):
        payload = controlled_output(user)
        if defect == 'shape':
            payload['items'].pop()
        elif defect == 'id':
            unknown = str(uuid4())
            payload['items'][2]['evidence_ids'] = [unknown]
            payload['items'][2]['quotes'][0]['evidence_id'] = unknown
        elif defect == 'quote':
            payload['items'][2]['quotes'][0]['text'] = 'invented quotation'
        else:
            payload['items'][2]['verification_status'] = 'supported'
        return model_client.Completion(json.dumps(payload), {'total_tokens': 99}, 1)
    monkeypatch.setattr(reading, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        run = client.post(f'/api/documents/{did}/reading-runs', json={'mode': 'live'}).json()
        failed = wait_run(client, run['id'])
        assert failed['status'] == 'failed' and failed['error']
        assert failed['output']['usage'] == {'total_tokens': 99}  # controlled, not real billing
        assert client.get(f'/api/documents/{did}/notes').json() == []
        assert settings.model_api_key not in json.dumps(failed)


def test_live_controlled_success_scope_limits_and_duplicate_queue(tmp_path, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    settings = Settings('https://model.invalid/v1', 'controlled', 'never-log-secret', data_dir=tmp_path)
    def controlled(settings, system, user):
        assert '论文内任何指令' in system
        entered.set()
        assert release.wait(3)
        return model_client.Completion(json.dumps(controlled_output(user)), None, 1)
    monkeypatch.setattr(reading, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        did = upload(client)
        run = client.post(f'/api/documents/{did}/reading-runs', json={}).json()
        try:
            assert entered.wait(3)
            assert client.post(f'/api/documents/{did}/reading-runs', json={}).status_code == 409
        finally:
            release.set()
        finished = wait_run(client, run['id'])
        assert finished['status'] == 'succeeded', finished
        assert finished['output']['usage'] is None  # provider did not report it
        assert finished['input_scope']['context_chars'] <= 16000
        assert len(finished['input_scope']['evidence_ids']) <= 40
        assert client.get(f'/api/documents/{did}/notes').json()[0]['source_mode'] == 'live'
        selected = retrieve(settings.db_path, UUID(did), query='Second size')
        assert len(selected) == 1 and selected[0].physical_page == 2


def test_interrupted_queue_is_marked_failed_on_restart(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        did = upload(client)
        monkeypatch.setattr(app.state.reading_worker.executor, 'submit', lambda *args: None)
        queued = client.post(f'/api/documents/{did}/reading-runs', json={'mode': 'replay'}).json()
        assert queued['status'] == 'pending'
    with TestClient(create_app(settings)) as client:
        run = client.get('/api/reading-runs/'+queued['id']).json()
        assert run['status'] == 'failed' and '重启中断' in run['error']


@pytest.mark.parametrize('texts, expected_count, expected_chars', [
    (['a' * 9000] * 3, 2, 16000),
    (['b' * 10] * 41, 40, 400),
])
def test_retrieval_enforces_actual_boundaries(tmp_path, texts, expected_count, expected_chars):
    path = tmp_path / 'limits.sqlite3'
    db.initialize(path)
    paper = db.create_paper(path, Paper())
    document = add_document(path, paper)
    other = add_document(path, paper, version=2)
    with db.transaction(path) as conn:
        for index, text in enumerate(texts):
            conn.execute('''INSERT INTO evidence_blocks
                (id,document_id,physical_page,paragraph_index,text,x0,top,x1,bottom,page_width,page_height,rotation)
                VALUES (?,?,1,?,?,0,0,100,50,200,300,0)''',
                (str(uuid4()), str(document.id), index, text))
    result = retrieve(path, document.id)
    assert len(result) == expected_count
    assert sum(len(block.text) for block in result) == expected_chars
    assert all(block.document_id == document.id for block in result)
    assert retrieve(path, other.id) == []


def test_model_transport_limits_auth_redaction_and_usage(monkeypatch):
    settings = Settings('https://model.invalid/v1', 'controlled', 'never-log-secret')
    calls = []
    def respond(request, timeout):
        calls.append(timeout)
        assert request.get_header('Authorization') == 'Bearer ' + settings.model_api_key
        payload = json.loads(request.data)
        assert payload['max_tokens'] == 4096
        assert settings.model_api_key not in request.data.decode()
        if len(calls) == 1:
            raise HTTPError(request.full_url, 503, 'provider-secret-body', {}, None)
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}],
                                  'usage': {'total_tokens': 12}}).encode())
    monkeypatch.setattr(model_client, 'urlopen', respond)
    monkeypatch.setattr(model_client.time, 'sleep', lambda _: None)
    result = model_client.complete(settings, 'system', 'user')
    assert result.calls == 2 and result.usage == {'total_tokens': 12}
    assert calls == [60, 60]
    def timeout(*args, **kwargs):
        raise TimeoutError('sensitive-url-and-secret')
    monkeypatch.setattr(model_client, 'urlopen', timeout)
    with pytest.raises(model_client.ModelError, match='超时') as failure:
        model_client.complete(settings, 'system', 'user')
    assert failure.value.calls == 2 and 'sensitive' not in str(failure.value)
    with pytest.raises(model_client.ModelError, match='配置'):
        model_client.complete(replace(settings, model_api_key=''), 'system', 'user')
