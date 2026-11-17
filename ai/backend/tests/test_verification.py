"""Injected examples are synthetic; controlled responses check orchestration, not model accuracy."""
from contextlib import closing
import json
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import db
from app.agents import verification
from app.agents.client import Completion, ModelError
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.schemas import AnalysisItem, EvidenceQuote, Note, Paper
from app.services.retrieval import context
from test_db import add_document
from test_pdf_import import make_pdf
from test_reading import wait_run

CASES = json.loads((PROJECT_ROOT/'fixtures/verification-cases.json').read_text(encoding='utf-8'))


def seed(client, settings):
    pdf = make_pdf([[(40, 750-index*35, text) for index, text in enumerate(CASES['source_lines'])]])
    response = client.post('/api/documents', files={'file': ('injected-errors-CC0.pdf', pdf)})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['report']['status'] == 'parsed', data['report']
    evidence = data['evidence']
    items = []
    for case in CASES['cases']:
        source = next(block for block in evidence if CASES['source_lines'][case['source_line']] in block['text'])
        quote = case.get('injected_quote', source['text'])
        items.append(AnalysisItem(field=case['field'], content_type='author_statement', content=case['claim'],
                                  evidence_ids=[source['id']], quotes=[EvidenceQuote(evidence_id=source['id'], text=quote)]))
    return db.create_note(settings.db_path, Note(paper_id=data['document']['paper_id'],
                          document_id=data['document']['id'], items=items, source_mode='unknown'))


def verdict_payload(user):
    payload = json.loads(user)
    source = payload['paper_content'][1]
    items = []
    for claim in payload['claims']:
        case = next(case for case in CASES['cases'] if case['field'] == claim['field'])
        status = 'not_reported' if claim['content'] == '未报告' else case['expected'][0]
        items.append({'item_id': claim['id'], 'status': status, 'reason': '受控响应：仅依据自制注入样例原文，不是准确率评测。',
                      'evidence_ids': [source['id']], 'quotes': [{'evidence_id': source['id'], 'text': source['text']}]})
    return {'items': items}


def revised_payload(user):
    claims = json.loads(user)['claims']
    return {'items': [{'id': item['id'], 'field': item['field'], 'content_type': 'author_statement',
                       'content': '未报告', 'verification_status': 'not_reported', 'evidence_ids': [], 'quotes': []}
                      for item in claims]}


def start(client, note, revise=True):
    response = client.post(f'/api/documents/{note.document_id}/verification-runs',
                           json={'base_note_id': str(note.id), 'revise': revise})
    assert response.status_code == 202, response.text
    return response.json()


def configured(tmp_path):
    return Settings('https://model.invalid/v1', 'controlled', 'never-log-secret', data_dir=tmp_path)


def test_bounded_revision_rechecks_only_problems_and_keeps_uncertainty(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    calls = []
    def controlled(settings, system, user):
        payload = json.loads(user)
        assert '91 percent' in ' '.join(block['text'] for block in payload['paper_content'])
        calls.append(payload)
        if 'issues' in payload:
            assert all(item['field'] not in {'goal', 'ablation'} for item in payload['claims'])
            result = revised_payload(user)
        else:
            result = verdict_payload(user)
            if len(calls) == 3:
                assert all(item['field'] not in {'goal', 'ablation'} for item in payload['claims'])
                result['items'][0].update(status='unchecked', reason='受控复查：原文仍不足，保留疑点。')
        return Completion(json.dumps(result), None, 1)
    monkeypatch.setattr(verification, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        base = seed(client, settings)
        run = wait_run(client, start(client, base)['id'])
        assert run['status'] == 'succeeded', run
        assert len(calls) == 3 and run['output']['revision_rounds'] == 1
        assert run['output']['api_calls'] == 3 and not run['output']['usage_complete']
        assert len(run['output']['unresolved_item_ids']) == 1
        assert len(run['output']['contexts']) == 2
        notes = client.get(f'/api/documents/{base.document_id}/notes').json()
        assert len(notes) == 2 and notes[0]['verification_run_id'] == run['id']
        assert notes[1] == base.model_dump(mode='json')
        control = next(item for item in notes[0]['items'] if item['field'] == 'goal')
        assert control['verification_status'] == 'supported'
        assert control['content'] == base.items[0].content and control['id'] == str(base.items[0].id)
        with closing(db.connect(settings.db_path)) as conn:
            steps = conn.execute('SELECT role,attempt,status FROM run_steps WHERE run_id=?', (run['id'],)).fetchall()
            assert {(row['role'], row['attempt'], row['status']) for row in steps} == {
                ('verification', 1, 'succeeded'), ('reading', 1, 'succeeded'), ('verification', 2, 'succeeded')}


@pytest.mark.parametrize('defect', ['duplicate', 'id', 'quote', 'status', 'confidence'])
def test_invalid_verdicts_fail_without_new_snapshot(tmp_path, monkeypatch, defect):
    settings = configured(tmp_path)
    def controlled(settings, system, user):
        result = verdict_payload(user)
        if defect == 'duplicate':
            result['items'][1] = result['items'][0]
        elif defect == 'id':
            result['items'][0]['item_id'] = str(uuid4())
        elif defect == 'quote':
            result['items'][0]['quotes'][0]['text'] = 'invented source quotation'
        elif defect == 'status':
            result['items'][0]['status'] = 'certain'
        else:
            result['items'][0]['confidence'] = .99
        return Completion(json.dumps(result), {'total_tokens': 12}, 1)
    monkeypatch.setattr(verification, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        base = seed(client, settings)
        run = wait_run(client, start(client, base)['id'])
        assert run['status'] == 'failed' and run['error']
        assert run['output']['api_calls'] == 1 and not run['output']['checks']
        assert len(client.get(f'/api/documents/{base.document_id}/notes').json()) == 1
        assert settings.model_api_key not in json.dumps(run)


def test_manual_content_is_never_revised_and_prior_supported_items_are_skipped(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    calls = []
    def controlled(settings, system, user):
        calls.append(json.loads(user))
        assert 'issues' not in calls[-1]
        assert all(item['field'] != 'goal' for item in calls[-1]['claims'])
        return Completion(json.dumps(verdict_payload(user)), {'total_tokens': 12}, 1)
    monkeypatch.setattr(verification, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        base = seed(client, settings)
        values = base.items[0].model_dump()
        values.update(verification_status='supported', verification_reason='受控既有通过条目')
        manual = db.create_note(settings.db_path, Note(paper_id=base.paper_id, document_id=base.document_id,
            parent_note_id=base.id, version=2, is_user_edited=True,
            items=[AnalysisItem.model_validate(values), *base.items[1:]]))
        run = wait_run(client, start(client, manual, revise=True)['id'])
        assert run['status'] == 'succeeded' and run['input_scope']['revision_limit'] == 0
        assert run['output']['revision_rounds'] == 0 and len(calls) == 1
        latest = client.get(f'/api/documents/{base.document_id}/notes').json()[0]
        assert [item['content'] for item in latest['items']] == [item.content for item in manual.items]
        assert latest['items'][0] == manual.items[0].model_dump(mode='json')
        assert latest['is_user_edited']


def test_concurrent_manual_save_prevents_verification_overwrite(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    entered, release = Event(), Event()
    def controlled(settings, system, user):
        entered.set()
        assert release.wait(4)
        return Completion(json.dumps(verdict_payload(user)), None, 1)
    monkeypatch.setattr(verification, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        base = seed(client, settings)
        queued = start(client, base, revise=False)
        try:
            assert entered.wait(4)
            saved = client.post(f'/api/documents/{base.document_id}/notes', json={
                'base_note_id': str(base.id), 'changes': {str(base.items[0].id): 'Concurrent human content'}})
            assert saved.status_code == 201
        finally:
            release.set()
        run = wait_run(client, queued['id'])
        assert run['status'] == 'failed' and '未覆盖人工修改' in run['error']
        assert len(run['output']['checks']) == 1
        assert client.get(f'/api/documents/{base.document_id}/notes').json()[0] == saved.json()
        assert client.post(f'/api/documents/{base.document_id}/verification-runs', json={'base_note_id': str(base.id)}).status_code == 409


def test_recheck_error_keeps_initial_findings_and_does_not_save_unverified_revision(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    count = 0
    def controlled(settings, system, user):
        nonlocal count
        count += 1
        if count == 3:
            raise ModelError('受控复查超时', calls=2)
        return Completion(json.dumps(revised_payload(user) if count == 2 else verdict_payload(user)), None, 1)
    monkeypatch.setattr(verification, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        base = seed(client, settings)
        run = wait_run(client, start(client, base)['id'])
        assert run['status'] == 'failed' and count == 3
        assert run['output']['api_calls'] == 4 and len(run['output']['checks']) == 1
        assert len(client.get(f'/api/documents/{base.document_id}/notes').json()) == 1
        with closing(db.connect(settings.db_path)) as conn:
            row = conn.execute("SELECT status FROM run_steps WHERE run_id=? AND role='verification' AND attempt=2", (run['id'],)).fetchone()
            assert row['status'] == 'failed'


@pytest.mark.parametrize('defect', ['id', 'quote'])
def test_invalid_revision_cannot_touch_passed_items_or_invent_sources(tmp_path, monkeypatch, defect):
    settings = configured(tmp_path)
    calls = []
    def controlled(settings, system, user):
        payload = json.loads(user)
        calls.append(payload)
        if 'issues' not in payload:
            return Completion(json.dumps(verdict_payload(user)), None, 1)
        result = revised_payload(user)
        if defect == 'id':
            result['items'][0]['id'] = calls[0]['claims'][0]['id']  # already-supported control
        else:
            source = payload['paper_content'][0]
            result['items'][0].update(content='Unsupported revision', verification_status='unchecked',
                evidence_ids=[source['id']], quotes=[{'evidence_id': source['id'], 'text': 'invented revision quote'}])
        return Completion(json.dumps(result), None, 1)
    monkeypatch.setattr(verification, 'complete', controlled)
    with TestClient(create_app(settings)) as client:
        base = seed(client, settings)
        run = wait_run(client, start(client, base)['id'])
        assert run['status'] == 'failed' and len(calls) == 2
        assert len(run['output']['checks']) == 1
        assert len(client.get(f'/api/documents/{base.document_id}/notes').json()) == 1


def test_context_prioritizes_late_originals_neighbours_and_rejects_wrong_versions(tmp_path):
    path = tmp_path/'context.sqlite3'
    db.initialize(path)
    paper = db.create_paper(path, Paper())
    document, other = add_document(path, paper), add_document(path, paper, 2)
    keys = [uuid4() for _ in range(51)]
    with db.transaction(path) as conn:
        for index, key in enumerate(keys):
            conn.execute('''INSERT INTO evidence_blocks
                (id,document_id,physical_page,paragraph_index,text,x0,top,x1,bottom,page_width,page_height,rotation)
                VALUES (?,?,1,?,?,0,0,100,50,200,300,0)''', (str(key), str(document.id), index, f'Original {index}'))
    result = context(path, document.id, [keys[-1]])
    assert len(result) == 40 and {keys[-1], keys[-2]} <= {block.id for block in result}
    with pytest.raises(ValueError, match='文件版本'):
        context(path, other.id, [keys[0]])
    with db.transaction(path) as conn:
        conn.execute('UPDATE evidence_blocks SET text=? WHERE id=?', ('a'*16001, str(keys[-1])))
    with pytest.raises(ValueError, match='上限'):
        context(path, document.id, [keys[-1]])


def test_verification_requires_configuration_and_restart_records_interruption(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        base = seed(client, settings)
        monkeypatch.setattr(app.state.reading_worker.executor, 'submit', lambda *args: None)
        queued = start(client, base)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        run = client.get('/api/reading-runs/'+queued['id']).json()
        assert run['status'] == 'failed' and '重启中断' in run['error']
        assert client.post(f'/api/documents/{base.document_id}/verification-runs', json={'base_note_id': str(base.id)}).status_code == 503
