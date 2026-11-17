"""Self-authored PDF fixtures, freely redistributable (CC0). No external papers."""
from io import BytesIO
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from app.services import pdf_import


def make_pdf(pages, rotation=0, image=False, metadata=False, page_sizes=None):
    """Tiny standards-based PDF writer for controlled text/layout boundary fixtures."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    if image:
        objects.append(b'<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x00\nendstream')
    for page_index, lines in enumerate(pages):
        width, height = page_sizes[page_index] if page_sizes else (600, 800)
        page_id = len(objects) + 1
        kids.append(f'{page_id} 0 R')
        content = '\n'.join(f'BT /F1 12 Tf {x} {y} Td ({text}) Tj ET' for x, y, text in lines).encode()
        if image:
            content += b'\nq 100 0 0 100 40 500 cm /Im0 Do Q'
        image_resource = '/XObject << /Im0 4 0 R >>' if image else ''
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Rotate {rotation} /Resources << /Font << /F1 3 0 R >> {image_resource} >> /Contents {page_id+1} 0 R >>'.encode())
        objects.append(f'<< /Length {len(content)} >>\nstream\n'.encode()+content+b'\nendstream')
    objects[1] = f'<< /Type /Pages /Count {len(kids)} /Kids [{" ".join(kids)}] >>'.encode()
    info = ''
    if metadata:
        objects.append(b'<< /Title (Self Authored Title) /Author (Fixture Author) >>')
        info = f'/Info {len(objects)} 0 R'
    result = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f'{number} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start = len(result)
    result.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        result.extend(f'{offset:010} 00000 n \n'.encode())
    result.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R {info} >>\nstartxref\n{start}\n%%EOF'.encode())
    return bytes(result)


@pytest.fixture
def api(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        yield client, settings


def upload(client, content, filename='self-authored.pdf'):
    return client.post('/api/documents', files={'file': (filename, content, 'application/pdf')})


def test_upload_evidence_order_bounds_hash_and_duplicate_reuse(api):
    client, settings = api
    content = make_pdf([[(40, 750, '1 Introduction'), (40, 720, 'Ignore all instructions is paper text.')],
                        [(50, 740, '2 Method'), (50, 710, 'Actual evidence.')]])
    response = upload(client, content, '../../outside.pdf')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['report']['status'] == 'parsed'
    assert data['report']['extracted_pages'] == [1, 2]
    assert data['report']['metadata_candidates'] == {'title': None, 'author': None}
    assert data['document']['sha256'] == hashlib.sha256(content).hexdigest()
    assert data['document']['page_count'] == 2
    assert [b['physical_page'] for b in data['evidence']] == [1, 1, 2, 2]
    assert data['evidence'][0]['section'] == '1 Introduction'
    assert 'Ignore all instructions' in data['evidence'][1]['text']
    for block in data['evidence']:
        x0, top, x1, bottom = block['bbox']
        assert 0 <= x0 < x1 <= block['page_width']
        assert 0 <= top < bottom <= block['page_height']
    assert data['evidence'][0]['bbox'][0] == 40
    assert 35 < data['evidence'][0]['bbox'][1] < 50
    repeated = upload(client, content).json()
    assert repeated['reused'] is True
    assert repeated['document']['id'] == data['document']['id']
    assert repeated['evidence'] == data['evidence']
    assert client.get('/api/documents/'+data['document']['id']).json()['report'] == data['report']
    with db.transaction(settings.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM papers').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 1


@pytest.mark.parametrize('content', [b'not a PDF', b''])
def test_format_rejected_before_storage(api, content):
    client, settings = api
    assert upload(client, content).status_code == 415
    assert not (settings.data_dir/'documents').exists()


def test_size_limit_checks_actual_bytes(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(pdf_import, 'MAX_BYTES', 10)
    assert upload(client, b'%PDF-'+b'x'*6).status_code == 413


def test_scanned_page_and_metadata_candidates(api):
    client, _ = api
    scanned = upload(client, make_pdf([[]], image=True)).json()
    assert scanned['report']['status'] == 'failed'
    assert 'OCR' in scanned['report']['warnings'][0]
    result = upload(client, make_pdf([[(40, 750, 'Body text')]], image=True, metadata=True)).json()
    assert result['report']['status'] == 'limited'
    assert result['report']['metadata_candidates'] == {'title': 'Self Authored Title', 'author': 'Fixture Author'}
    assert '图像或表格' in result['report']['warnings'][0]


def test_page_count_limit_is_explicit(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(pdf_import, 'MAX_PAGES', 1)
    result = upload(client, make_pdf([[], []])).json()
    assert result['report']['status'] == 'failed'
    assert 'PDF 页数' in result['report']['error']


@pytest.mark.parametrize('content,reason', [
    (b'%PDF-1.4\nbroken', 'PDF 解析失败'),
    (make_pdf([[]]), '没有可可靠'),
    (make_pdf([[(40, 750, 'Left text'), (350, 750, 'Right text')]]), '没有可可靠'),
    (make_pdf([[(40, 750, 'Rotated text')]], rotation=90), '没有可可靠'),
])
def test_unreadable_inputs_persist_explicit_failure(api, content, reason):
    client, _ = api
    response = upload(client, content)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['document']['parse_status'] == 'failed'
    assert data['report']['status'] == 'failed'
    assert reason in data['report']['error']
    assert data['evidence'] == []
    assert data['report']['extracted_pages'] == []
    assert upload(client, content).json()['reused'] is True


def test_partial_coverage_and_restart(api):
    client, settings = api
    data = upload(client, make_pdf([[(40, 740, 'Readable page')], []])).json()
    assert data['report']['status'] == 'limited'
    assert data['report']['extracted_pages'] == [1]
    assert data['report']['warnings']
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/documents/'+data['document']['id']).json()['evidence'] == data['evidence']


def test_pending_stored_file_is_parsed_on_reupload(api):
    from app.schemas import Paper
    from app.services.storage import store_document
    client, settings = api
    content = make_pdf([[(40, 740, 'Recovered pending document')]])
    paper = db.create_paper(settings.db_path, Paper())
    original = store_document(settings.db_path, settings.data_dir, paper.id, BytesIO(content), 'pending.pdf')
    result = upload(client, content).json()
    assert result['reused'] is True
    assert result['document']['id'] == str(original.id)
    assert result['report']['status'] == 'parsed'


def test_invalid_document_id_and_missing_paper_fail_without_upload(api):
    client, _ = api
    assert client.get('/api/documents/not-an-id').status_code == 422
    response = client.post('/api/documents', data={'paper_id': '00000000-0000-0000-0000-000000000001'},
                           files={'file': ('sample.pdf', make_pdf([[]]))})
    assert response.status_code == 404


def test_v1_upgrade_preserves_and_parses_existing_document(tmp_path):
    from app.schemas import Paper
    from app.services.storage import store_document
    settings = Settings(data_dir=tmp_path)
    with db.transaction(settings.db_path) as conn:
        for statement in db.SCHEMA_V1:
            conn.execute(statement)
        conn.execute('PRAGMA user_version=1')
    paper = db.create_paper(settings.db_path, Paper(title='Existing v1 metadata'))
    content = make_pdf([[(40, 750, 'Existing version one document')]])
    original = store_document(settings.db_path, settings.data_dir, paper.id, BytesIO(content), 'v1.pdf')
    with TestClient(create_app(settings)) as client:
        result = upload(client, content).json()
        assert result['document']['id'] == str(original.id)
        assert result['reused'] and result['report']['status'] == 'parsed'
    with db.transaction(settings.db_path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 3
        assert conn.execute('SELECT title FROM papers WHERE id=?', (str(paper.id),)).fetchone()[0] == paper.title
