"""Self-authored saved snapshots; export must never call a model or select a newer version."""
from hashlib import sha256
from io import BytesIO
import json
from uuid import uuid4

from docx import Document
from docx.table import Table
from fastapi.testclient import TestClient
import pytest

from app import db
from app.config import Settings
from app.main import create_app
from app.schemas import AnalysisItem, EvidenceQuote, Note
from test_pdf_import import make_pdf
from test_reading import upload


def saved_note(client, settings):
    did = upload(client, make_pdf([[(40,750,'Self-authored export evidence.'), (40,710,'No scientific result is claimed.')]]))
    doc = client.get('/api/documents/'+did).json()
    evidence = doc['evidence'][0]
    paper_id = doc['document']['paper_id']
    with db.transaction(settings.db_path) as conn:
        conn.execute('UPDATE papers SET title=?,authors=?,abstract=?,tags=? WHERE id=?',
                     ('中文标题 | <script> *研究*', json.dumps(['自制作者甲', 'Author B']), '自制摘要\r\n下一行', json.dumps(['导出测试']), paper_id))
    note = Note(paper_id=paper_id, document_id=did, source_mode='replay', items=[
        AnalysisItem(field='method', content_type='author_statement', content='中文方法 | *literal* <img src=x> & [link](https://invalid.test)\n第二行',
                     evidence_ids=[evidence['id']], quotes=[EvidenceQuote(evidence_id=evidence['id'], text=evidence['text'])],
                     verification_status='supported', verification_reason='受控保存的核查理由；不代表真实模型验收。'),
        AnalysisItem(field='result', content_type='system_analysis', content='本次输入范围未报告', verification_status='not_reported')],
        writing_items=[AnalysisItem(field='limitation_discussion', content_type='system_analysis', content='写作分析保留人工判断', verification_status='unchecked')])
    db.create_note(settings.db_path, note)
    return note, doc


def prepared(client, selection):
    response = client.post('/api/exports', json=selection)
    assert response.status_code == 201, response.text
    info = response.json()
    md = client.get(info['markdown_url'])
    word = client.get(info['word_url'])
    assert md.status_code == word.status_code == 200
    assert md.headers['content-disposition'].endswith('.md"')
    assert word.headers['content-disposition'].endswith('.docx"')
    assert 'wordprocessingml' in word.headers['content-type']
    return info, md.content, word.content


def assert_same_snapshot(settings, info, md, word):
    snapshot = (settings.data_dir/'exports'/info['id']/'snapshot.json').read_bytes()
    assert sha256(snapshot).hexdigest() == info['content_sha256']
    blocks = json.loads(snapshot)['blocks']
    actual = []
    for element in Document(BytesIO(word)).iter_inner_content():
        if isinstance(element, Table):
            actual.append(('table', [[cell.text for cell in row.cells] for row in element.rows]))
        else:
            actual.append(('text', element.text))
    expected = [('table', block['rows']) if block['kind'] == 'table' else ('text', block['text']) for block in blocks]
    # The only extra paragraphs are the shared identity/time/hash, immediately after the title.
    assert actual[:1] + actual[4:] == expected
    for identity in (info['id'], info['created_at'], info['content_sha256']):
        assert identity in md.decode() and any(identity in value for kind, value in actual if kind == 'text')
    return '\n'.join(value for kind, value in actual if kind == 'text')


def test_note_downloads_freeze_versions_metadata_sources_and_survive_restart(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('Export called a model')
    # Patch all role entry points: no export path may regenerate saved content.
    from app.agents import reading, writing, verification, ideas
    for agent in (reading, writing, verification, ideas):
        monkeypatch.setattr(agent, 'complete', forbidden)
    with TestClient(create_app(settings)) as client:
        note, doc = saved_note(client, settings)
        response = client.post(f'/api/documents/{note.document_id}/notes', json={
            'base_note_id': str(note.id), 'changes': {str(note.items[0].id): '人工修订后的正文：尚未核查'}})
        assert response.status_code == 201, response.text
        manual = response.json()
        old_info, old_md, old_word = prepared(client, {'note_id': str(note.id)})
        info, md, word = prepared(client, {'note_id': manual['id']})
        text = assert_same_snapshot(settings, info, md, word)
        old_text = assert_same_snapshot(settings, old_info, old_md, old_word)
        assert '人工修订：是' in text and '人工修订后的正文：尚未核查' in text
        assert '人工修订：否' in old_text and note.items[0].content in old_text
        for required in ['作者陈述', '系统判断', '本次范围未报告', '仍待核查', '样例回放（未调用模型）', '写作分析保留人工判断',
                         '物理页码（从1开始）：1', '文件版本：1', '章节候选：', '区域坐标', doc['document']['sha256'], doc['evidence'][0]['text'],
                         '核查任务：未提供', '自制作者甲', '中文标题 | <script> *研究*']:
            assert required in text and required.replace('<script>', '&lt;script&gt;').replace(' | ', ' \\| ').replace('*研究*', '\\*研究\\*') in md.decode()
        assert '<img src=x>' not in old_md.decode()
        assert '&lt;img src=x&gt;' in old_md.decode() and '\\[link\\]' in old_md.decode()
        assert 'https://invalid.test' in old_text  # Preserved as literal source content, never fetched.
        assert '<script>' not in md.decode()
        with db.transaction(settings.db_path) as conn:
            conn.execute('UPDATE papers SET title=? WHERE id=?', ('后来修改的题目', str(note.paper_id)))
        assert client.get(info['markdown_url']).content == md
        assert client.get(info['word_url']).content == word
    with TestClient(create_app(settings)) as client:
        assert client.get(info['word_url']).content == word
        assert client.get(info['markdown_url']).content == md


def test_selected_ideas_keep_exact_versions_experiments_and_legacy_limitations(tmp_path):
    settings = Settings(data_dir=tmp_path)
    from app.agents.ideas import IdeaDraft
    from app.services import ideas
    from test_ideas import plan
    with TestClient(create_app(settings)) as client:
        note, doc = saved_note(client, settings)
        facts = [{'note_id': str(note.id), 'item_id': str(note.items[0].id), 'document_id': str(note.document_id),
                  'paper_id': str(note.paper_id), 'evidence_ids': [str(key) for key in note.items[0].evidence_ids]}]
        draft = IdeaDraft(question='中文设想与未验证实验', hypothesis='假设尚待验证', derivation='从来源条目推导', difference='拟议差异',
                          experiment=plan(), resources='资源待确认', failure_conditions='失败条件', novelty_checks=['尚需外部查新'],
                          sources=[{'note_id': str(note.id), 'item_id': str(note.items[0].id)}])
        with db.transaction(settings.db_path) as conn:
            first = ideas.insert(conn, draft, facts)
            second = ideas.insert(conn, draft.model_copy(update={'question': '人工新版本研究问题'}), facts, base=first, status='confirmed')
            conn.execute('UPDATE papers SET removed=1 WHERE id=?', (str(note.paper_id),))
            conn.execute('UPDATE ideas SET experiment_json=NULL WHERE id=?', (str(second.id),))
            conn.execute('UPDATE idea_sources SET item_id=NULL WHERE idea_id=?', (str(second.id),))
            raw = conn.execute('SELECT report_json FROM parse_reports WHERE document_id=?', (str(note.document_id),)).fetchone()[0]
            coverage = json.loads(raw)
            coverage.update(status='limited', warnings=['自制受限样例：双栏顺序不能保证'])
            conn.execute('UPDATE parse_reports SET report_json=? WHERE document_id=?', (json.dumps(coverage), str(note.document_id)))
        info, md, word = prepared(client, {'idea_ids': [str(second.id), str(first.id)]})
        text = assert_same_snapshot(settings, info, md, word)
        assert text.index('人工新版本研究问题') < text.index('中文设想与未验证实验')
        for required in ['用户已确认', '生成任务：未提供', '旧记录没有结构化实验', '旧来源未保存具体条目', '文献已软移除：是（来源保留）',
                         '物理页码（从1开始）：1', '文件SHA256：'+doc['document']['sha256'], '唯一变化因素：mask_ratio', '待确认条件：', '尚需外部查新',
                         '解析覆盖状态：limited', '自制受限样例：双栏顺序不能保证']:
            assert required in text and required.replace('mask_ratio', 'mask\\_ratio') in md.decode()
        tables = Document(BytesIO(word)).tables
        assert len(tables) == 1
        assert ['mask_ratio', '0', '0.15'] in [[cell.text for cell in row.cells] for row in tables[0].rows]


def test_export_rejects_invalid_selection_missing_versions_control_characters_and_corrupt_files(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        key = str(uuid4())
        for selection in ({}, {'note_id': key, 'idea_ids': [key]}, {'idea_ids': [key, key]}, {'idea_ids': [str(uuid4()) for _ in range(11)]}):
            assert client.post('/api/exports', json=selection).status_code == 422
        for selection in ({'note_id': key}, {'idea_ids': [key]}):
            assert client.post('/api/exports', json=selection).status_code == 404
        assert client.get(f'/api/exports/{key}/docx').status_code == 404
        assert client.get(f'/api/exports/{key}/pdf').status_code == 422
        note, _ = saved_note(client, settings)
        with db.transaction(settings.db_path) as conn:
            conn.execute('UPDATE notes SET items_json=? WHERE id=?',
                         (json.dumps([note.items[0].model_copy(update={'content': '不可丢弃\x01字符'}).model_dump(mode='json')]), str(note.id)))
        rejected = client.post('/api/exports', json={'note_id': str(note.id)})
        assert rejected.status_code == 409 and '控制字符' in rejected.text
        assert not (tmp_path/'exports').exists()
        with db.transaction(settings.db_path) as conn:
            conn.execute('UPDATE notes SET items_json=? WHERE id=?', (json.dumps([item.model_dump(mode='json') for item in note.items]), str(note.id)))
        info, _, _ = prepared(client, {'note_id': str(note.id)})
        (tmp_path/'exports'/info['id']/'report.docx').write_bytes(b'corrupted')
        assert client.get(info['word_url']).status_code == 409
        assert client.get(info['markdown_url']).status_code == 200
        (tmp_path/'exports'/info['id']/'manifest.json').unlink()
        assert client.get(info['markdown_url']).status_code == 404


def test_write_failure_does_not_publish_partial_pair(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    from pathlib import Path
    original = Path.write_bytes
    def fail_word(path, data):
        if path.name == 'report.docx':
            raise OSError('controlled full disk')
        return original(path, data)
    with TestClient(create_app(settings)) as client:
        note, _ = saved_note(client, settings)
        monkeypatch.setattr(Path, 'write_bytes', fail_word)
        result = client.post('/api/exports', json={'note_id': str(note.id)})
        assert result.status_code == 500 and '写入失败' in result.text
        assert list((tmp_path/'exports').iterdir()) == []
