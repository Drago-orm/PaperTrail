from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from test_pdf_import import make_pdf


def test_upload_edit_filter_remove_restore_and_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    content = make_pdf([[(40, 750, '1 Introduction')], [(40, 550, 'Second size')]], page_sizes=[(600,800),(420,600)])
    with TestClient(create_app(settings)) as client:
        result = client.post('/api/documents', files={'file': ('self-authored.pdf', content)}).json()
        pid = result['document']['paper_id']
        did = result['document']['id']
        assert [(b['page_width'], b['page_height']) for b in result['evidence']] == [(600,800),(420,600)]
        assert client.get('/api/papers').json()[0]['id'] == pid
        metadata = {'title': "O'Reilly Research 100%", 'authors': ['Alice'], 'abstract': 'Vision research',
                    'keywords': ['attention'], 'tags': ['review'], 'reading_status': 'read'}
        assert client.patch('/api/papers/'+pid, json=metadata).status_code == 200
        filters = {'q': 'research', 'title': "O'Reilly", 'author': 'alice', 'abstract': 'Vision',
                   'keyword': 'attention', 'tag': 'review', 'reading_status': 'read'}
        assert len(client.get('/api/papers', params=filters).json()) == 1
        for key in filters:
            assert client.get('/api/papers', params={**filters, key: 'mismatch'}).json() == []
        assert len(client.get('/api/papers', params={'q': '%'}).json()) == 1
        assert client.get('/api/papers', params={'q': "' OR 1=1 --"}).json() == []
        assert client.get('/api/papers', params={'reading_status': 'unread'}).json() == []
        assert client.get(f'/api/documents/{did}/file').content == content
        assert client.patch('/api/papers/'+pid, json={'id': str(uuid4())}).status_code == 422
        assert client.patch('/api/papers/'+pid, json={'reading_status': 'invented'}).status_code == 422
        assert client.put(f'/api/papers/{pid}/visibility?removed=true').status_code == 200
        assert client.get('/api/papers').json() == []
        assert client.get('/api/papers?removed=true').json()[0]['id'] == pid
        assert client.get('/api/documents/'+did).json()['evidence'] == result['evidence']
        assert client.get(f'/api/documents/{did}/file').content == content
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/papers?removed=true').json()[0]['title'] == metadata['title']
        client.put(f'/api/papers/{pid}/visibility?removed=false')
        saved = client.get('/api/papers').json()[0]
        assert all(saved[key] == value for key, value in metadata.items())


def test_remove_only_selected_and_missing_ids(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        ids = [client.post('/api/documents', files={'file': ('sample.pdf', make_pdf([[(40, 750, text)]]))}).json()['document']['paper_id']
               for text in ['First fixture', 'Second fixture']]
        client.put(f'/api/papers/{ids[0]}/visibility?removed=true')
        assert [p['id'] for p in client.get('/api/papers').json()] == [ids[1]]
        assert client.put(f'/api/papers/{uuid4()}/visibility?removed=true').status_code == 404
        assert client.get(f'/api/documents/{uuid4()}/file').status_code == 404
