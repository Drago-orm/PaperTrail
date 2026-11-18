"""Self-authored fixtures are CC0-1.0; identifiers are illustrative, never resolved."""
from pathlib import Path
from uuid import uuid4
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app import db
from app.config import Settings
from app.main import create_app
from app.schemas import Paper
from app.services import bibliography

FIXTURES = Path(__file__).parent / 'fixtures'


def upload(client, content, format, token=None):
    data = {'format': format}
    if token: data['confirmation_token'] = token
    return client.post('/api/bibliography/'+('import' if token else 'preview'),
                       files={'file': ('wrong-extension.pdf', content)}, data=data)


def import_content(client, content, format):
    preview = upload(client, content, format)
    assert preview.status_code == 200, preview.text
    result = upload(client, content, format, preview.json()['confirmation_token'])
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.parametrize('format', ['bib', 'ris'])
def test_roundtrip_characters_authors_identifiers_missing_and_duplicates(tmp_path, monkeypatch, format):
    import urllib.request
    def forbidden(*args, **kwargs): pytest.fail('Metadata exchange must not access external services')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    settings = Settings(data_dir=tmp_path)
    content = (FIXTURES / f'metadata.{format}').read_bytes()
    with TestClient(create_app(settings)) as client:
        before = upload(client, b'\xef\xbb\xbf'+content, format)
        assert before.status_code == 200, before.text
        value = before.json()
        assert client.get('/api/papers').json() == []
        assert not value['imported'] and len(value['records']) == 3
        assert value['records'][0]['candidates'][0]['id'] == 'upload-2'
        assert value['records'][0]['unmapped']
        imported = upload(client, b'\xef\xbb\xbf'+content, format, value['confirmation_token']).json()
        ids = [entry['id'] for entry in imported['records']]
        originals = {p['id']: p for p in client.get('/api/papers').json()}
        assert originals[ids[0]]['authors'] == ['张三', 'Müller, Jörg', 'Research and Development']
        assert originals[ids[2]]['doi'] == '' and originals[ids[2]]['year'] is None
        assert all(not p['documents'] for p in originals.values())
        assert len(client.get(f'/api/papers/{ids[0]}/duplicates').json()) == 1
        exported = client.post('/api/bibliography/export', json={'format':format, 'paper_ids':ids})
        assert exported.status_code == 200, exported.text
        saved = exported.json()
        assert saved['count'] == 3 and saved['warnings']
        copied = import_content(client, saved['text'].encode('utf-8'), format)
        all_papers = {p['id']:p for p in client.get('/api/papers').json()}
        for old, new in zip(ids, [e['id'] for e in copied['records']], strict=True):
            for field in ['title', 'authors', 'doi', 'year', 'abstract', 'identifiers']:
                assert originals[old][field] == all_papers[new][field], field
        assert any(not c['id'].startswith('upload-') for c in copied['records'][0]['candidates'])
        with db.transaction(settings.db_path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 0
            for table in ['notes', 'evidence_blocks', 'runs', 'merge_events']:
                assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    with TestClient(create_app(settings)) as client:
        assert len(client.get('/api/papers').json()) == 6
        assert client.get('/api/papers').json()[0]['identifiers'] is not None


def test_cross_format_preserves_identifier_values_without_guessing_types(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        value = import_content(client, (FIXTURES/'metadata.bib').read_bytes(), 'bib')
        original = value['records'][0]['metadata']
        rid = value['records'][0]['id']
        output = client.post('/api/bibliography/export',json={'format':'ris','paper_ids':[rid]}).json()
        imported = import_content(client, output['text'].encode(), 'ris')['records'][0]
        for key in ['title','authors','year','doi','abstract']: assert original[key] == imported['metadata'][key]
        flat = lambda mapping: sorted(v for values in mapping.values() for v in values)
        assert flat(original['identifiers']) == flat(imported['metadata']['identifiers'])
        assert 'serial_number' in imported['metadata']['identifiers'] and 'isbn' not in imported['metadata']['identifiers']
        assert any('SN' in warning and 'AN' in warning for warning in output['warnings'])
        back = client.post('/api/bibliography/export',json={'format':'bib','paper_ids':[imported['id']]})
        # Two SN values cannot fit a single BibTeX field; do not silently join/truncate them.
        assert back.status_code == 409 and '多个值' in back.json()['detail']


@pytest.mark.parametrize('format,content', [
    ('bib', b''), ('bib', b'not a bibliography'), ('bib', b'@misc{x,title={broken}'),
    ('bib', b'@misc{x,title={one},title={two}}'), ('bib', b'@misc{x,title={a}}\n@misc{x,title={b}}'),
    ('bib', b'@misc{x,Title={one},title={two}}'), ('bib', b'@misc{x,title=undefined}'),
    ('bib', b'@string{a="Title"}\n@misc{x,title=a # { addition}}'),
    ('bib', b'@misc{x,year={forthcoming}}'), ('bib', b'\xff\xfe'), ('bib', b'%PDF-1.7\x00'),
    ('ris', b'TY  - JOUR\nTI  - Missing end'), ('ris', b'TI  - No start\nER  - \n'),
    ('ris', b'TY  - GEN\nTI  - one\nTI  - two\nER  - \n'),
    ('ris', b'TY  - GEN\nAU  - A\nA1  - B\nER  - \n'),
    ('ris', b'TY  - GEN\nTI  - good\nER  - \ntrailing garbage'),
    ('ris', b'@misc{x,title={wrong format}}'), ('bib', b'TY  - GEN\nER  - \n'),
])
def test_invalid_file_is_atomic_and_explicit(tmp_path, format, content):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = upload(client, content, format)
        assert response.status_code in [409,422], response.text
        assert response.json()['detail']
        assert client.get('/api/papers').json() == []


def test_limits_consent_stale_preview_and_rollback(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    content = (FIXTURES/'metadata.bib').read_bytes()
    with TestClient(create_app(settings)) as client:
        assert upload(client, b'x'*(bibliography.MAX_BYTES+1), 'bib').status_code == 409
        many = '\n'.join(f'@misc{{k{i},title={{T{i}}}}}' for i in range(101)).encode()
        assert upload(client, many, 'bib').status_code == 409
        value = upload(client, content, 'bib').json()
        assert upload(client, content, 'bib', '0'*64).status_code == 409
        assert upload(client, content+b' ', 'bib', value['confirmation_token']).status_code == 409
        db.create_paper(settings.db_path, Paper(title='Changed library'))
        assert upload(client, content, 'bib', value['confirmation_token']).status_code == 409
        original = db.insert_paper
        calls = []
        def failure(conn, paper):
            calls.append(paper)
            if len(calls) == 2: raise sqlite3.OperationalError('controlled write failure')
            return original(conn, paper)
        monkeypatch.setattr(db, 'insert_paper', failure)
        current = upload(client, content, 'bib').json()
        failed = upload(client, content, 'bib', current['confirmation_token'])
        assert failed.status_code == 500 and '事务未完成' in failed.json()['detail']
        assert len(client.get('/api/papers').json()) == 1


def test_identifier_merge_undo_and_old_database_migration(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with db.transaction(settings.db_path) as conn:
        for version, migration in enumerate(db.MIGRATIONS[:7], 1):
            for statement in migration: conn.execute(statement)
            conn.execute(f'PRAGMA user_version={version}')
        conn.execute('INSERT INTO papers(id,title,abstract,authors,keywords,tags,tasks,methods,datasets,reading_status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (str(uuid4()), 'Old paper', '', '[]', '[]', '[]', '[]', '[]', '[]', 'unread', '2026-01-01T00:00:00Z'))
    with TestClient(create_app(settings)) as client:
        old = client.get('/api/papers').json()[0]
        assert old['identifiers'] == {}
        records = import_content(client, (FIXTURES/'metadata.bib').read_bytes(), 'bib')['records']
        left, right = old['id'], records[0]['id']
        view = client.get('/api/paper-comparison', params={'left_id':left,'right_id':right}).json()
        fields = {key: 'left' for key in db.PAPER_EDITABLE};fields['identifiers'] = 'right'
        merged = client.post('/api/merges',json={'left_id':left,'right_id':right,'token':view['token'],'fields':fields})
        assert merged.status_code == 201, merged.text
        primary = next(p for p in client.get('/api/papers').json() if p['id'] == left)
        assert primary['identifiers'] == records[0]['metadata']['identifiers']
        assert client.post('/api/bibliography/export',json={'format':'bib','paper_ids':[right]}).status_code == 409
        assert client.post('/api/merges/'+merged.json()['id']+'/undo').status_code == 200
        primary = next(p for p in client.get('/api/papers').json() if p['id'] == left)
        assert primary['identifiers'] == {}
        assert client.post('/api/bibliography/export',json={'format':'bib','paper_ids':[right]}).status_code == 200


def test_export_missing_invalid_and_unrepresentable_values(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/bibliography/export',json={'format':'bib','paper_ids':[]}).status_code == 422
        assert client.post('/api/bibliography/export',json={'format':'bib','paper_ids':[str(uuid4())]}).status_code == 404
        p = db.create_paper(settings.db_path, Paper(title='Unbalanced { brace',abstract='First\nSecond'))
        assert client.post('/api/bibliography/export',json={'format':'bib','paper_ids':[str(p.id)]}).status_code == 409
        assert client.post('/api/bibliography/export',json={'format':'ris','paper_ids':[str(p.id)]}).status_code == 409
        assert client.post('/api/bibliography/export',json={'format':'ris','paper_ids':[str(p.id)]*2}).status_code == 422


def test_case_string_resolution_and_ris_year_aliases_are_explicit():
    entries, warnings = bibliography.parse(b'@comment{a@example.invalid}\n@string{t="Title"}\n@misc{x,Title=t,Author={Alice and {Research and Development}},Year=2024}', 'bib')
    assert entries[0]['metadata']['title'] == 'Title'
    assert entries[0]['metadata']['authors'] == ['Alice', 'Research and Development']
    assert any('string' in warning for warning in warnings)
    entries, warnings = bibliography.parse(b'TY  - GEN\nTI  - Date\nPY  - 2024\nY1  - 2024/01/03\nER  - \n', 'ris')
    assert entries[0]['metadata']['year'] == 2024
    assert any('月' in warning for warning in warnings)
