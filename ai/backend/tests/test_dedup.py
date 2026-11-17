"""Self-authored PDFs and controlled sources; dedup never judges scientific equivalence."""
from contextlib import closing
from io import BytesIO
import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app import db
from app.config import Settings
from app.main import create_app
from app.schemas import Paper
from app.services.storage import store_document
from test_pdf_import import make_pdf


def upload(client, text, **metadata):
    content = make_pdf([[(40,750,text)]])
    response = client.post('/api/documents', files={'file':('self-authored.pdf',content)})
    assert response.status_code == 200, response.text
    doc = response.json()['document']
    assert client.patch('/api/papers/'+doc['paper_id'],json=metadata).status_code == 200
    return doc, content


def compare(client, left, right):
    result = client.get('/api/paper-comparison',params={'left_id':left,'right_id':right})
    assert result.status_code == 200,result.text
    return result.json()


def body(view, **extra):
    return {'left_id':view['left']['id'],'right_id':view['right']['id'],'token':view['token'],**extra}


def merge(client, left, right, selected=None):
    view=compare(client,left,right)
    fields={key:'left' for key in db.PAPER_EDITABLE}
    fields.update(selected or {})
    result=client.post('/api/merges',json=body(view,fields=fields))
    assert result.status_code==201,result.text
    return result.json()


def test_hash_title_doi_year_candidates_version_and_nonduplicate_decisions(tmp_path,monkeypatch):
    from app.agents import reading,writing,verification,ideas
    def forbidden(*args,**kwargs):pytest.fail('Dedup called a model')
    for agent in (reading,writing,verification,ideas):monkeypatch.setattr(agent,'complete',forbidden)
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        first,content=upload(client,'Self-authored preprint',title='Graph—Learning: A Study',authors=['Alice Smith'],year=2024,doi='https://doi.org/10.1234/Example')
        second,_=upload(client,'Self-authored journal version',title='ＧＲＡＰＨ learning a study',authors=['alice smith'],year=2025,doi='DOI: 10.1234/example')
        left,right=first['paper_id'],second['paper_id']
        reused=client.post('/api/documents',files={'file':('renamed.pdf',content)}).json()
        assert reused['reused'] and reused['document']['id']==first['id']
        assert len(client.get('/api/papers').json())==2
        candidate=client.get(f'/api/papers/{left}/duplicates').json()[0]
        assert candidate['id']==right and candidate['kind']=='suspected'
        assert any('DOI相同' in reason for reason in candidate['reasons'])
        assert any('题目相同' in reason for reason in candidate['reasons'])
        view=compare(client,left,right)
        assert client.put('/api/paper-relations',json=body(view,kind='version')).status_code==200
        assert len(client.get('/api/papers').json())==2  # Preprint and journal files remain separate.
        assert compare(client,left,right)['relation']=='version'
        assert client.put('/api/paper-relations',json=body(view,kind='distinct')).status_code==409  # stale relation token
        view=compare(client,left,right)
        assert client.put('/api/paper-relations',json=body(view,kind='distinct')).status_code==200
        assert client.get(f'/api/papers/{left}/duplicates').json()==[]
        assert client.get(f'/api/papers/{left}/duplicates?include_distinct=true').json()[0]['relation']=='distinct'
        distinct=compare(client,left,right)
        assert client.post('/api/merges',json=body(distinct,fields={key:'left' for key in db.PAPER_EDITABLE})).status_code==409
        assert client.put('/api/paper-relations',json=body(distinct,kind=None)).status_code==200
        other,_=upload(client,'Different scientific problem fixture',title='Graph Learning for Medicine',authors=['Different Author'],abstract='A different task and protocol.',year=2024)
        manual=compare(client,left,other['paper_id'])
        assert manual['right']['abstract']=='A different task and protocol.'
        assert client.put('/api/paper-relations',json=body(manual,kind='related')).status_code==200
        # Legacy duplicate owners can still be explained by exact attachment hashes.
        legacy=db.create_paper(settings.db_path,Paper(title='Legacy same bytes'))
        store_document(settings.db_path,tmp_path,legacy.id,BytesIO(content),'legacy.pdf')
        assert compare(client,left,str(legacy.id))['kind']=='exact_file'
        assert client.patch('/api/papers/'+left,json={'year':'2024'}).status_code==422
        assert client.patch('/api/papers/'+left,json={'year':True}).status_code==422
        assert client.get('/api/paper-comparison',params={'left_id':left,'right_id':left}).status_code==409
        assert client.get('/api/paper-comparison',params={'left_id':left,'right_id':str(uuid4())}).status_code==404


def test_merge_undo_keeps_new_attachments_unrelated_edits_and_original_file_versions(tmp_path):
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        a,bytes_a=upload(client,'Original A',title='Primary',authors=['A'],tags=['original'])
        b,bytes_b=upload(client,'Original B',title='Secondary',authors=['B'],doi='10.1234/b',year=2025)
        left,right=a['paper_id'],b['paper_id']
        stale=compare(client,left,right)
        client.patch('/api/papers/'+right,json={'abstract':'Updated after comparison'})
        assert client.post('/api/merges',json=body(stale,fields={key:'left' for key in db.PAPER_EDITABLE})).status_code==409
        assert client.get(f'/api/papers/{left}/merges').json()==[]
        event=merge(client,left,right,{'title':'right','authors':'right','doi':'right','year':'right'})
        listing=client.get('/api/papers').json()
        assert len(listing)==1 and listing[0]['title']=='Secondary'
        assert listing[0]['doi']=='10.1234/b' and listing[0]['year']==2025
        assert {(doc['id'],doc['paper_id'],doc['version']) for doc in listing[0]['documents']}=={(a['id'],left,1),(b['id'],right,1)}
        assert client.patch('/api/papers/'+right,json={'title':'hidden edit'}).status_code==409
        assert client.put(f'/api/papers/{right}/visibility?removed=true').status_code==409
        fresh=make_pdf([[(40,750,'New original B attachment after merge')]])
        new=client.post('/api/documents',data={'paper_id':right},files={'file':('new.pdf',fresh)}).json()['document']
        assert new['paper_id']==right and new['version']==2
        assert len(client.get('/api/papers').json()[0]['documents'])==3
        client.patch('/api/papers/'+left,json={'tags':['edited after merge']})
        client.put(f'/api/papers/{left}/visibility?removed=true')
        assert client.post(f"/api/merges/{event['id']}/undo").status_code==200
        restored=client.get('/api/papers?removed=true').json()[0]
        assert restored['id']==left and restored['title']=='Primary' and restored['authors']==['A']
        assert restored['tags']==['edited after merge'] and restored['removed']
        secondary=client.get('/api/papers').json()[0]
        assert secondary['id']==right and {d['id'] for d in secondary['documents']}=={b['id'],new['id']}
        for doc,data in [(a,bytes_a),(b,bytes_b),(new,fresh)]:
            assert client.get('/api/documents/'+doc['id']+'/file').content==data
        assert client.post(f"/api/merges/{event['id']}/undo").status_code==409
    with TestClient(create_app(settings)) as client:
        assert client.get(f'/api/papers/{left}/merges').json()[0]['status']=='reverted'


def test_new_ideas_notes_and_evidence_survive_merge_undo_without_reparenting(tmp_path,monkeypatch):
    from test_ideas import setup,supported,proposal
    from app.services.reading_runs import list_notes
    settings=Settings('https://controlled.invalid/v1','controlled','test-only',data_dir=tmp_path)
    setup(settings,monkeypatch)
    with TestClient(create_app(settings)) as client:
        first,one=supported(client,settings)
        second,two=supported(client,settings,make_pdf([[(40,750,'Self-authored independent second source')]]))
        notes=[list_notes(settings,key)[0] for key in (first,second)]
        left,right=(str(note.paper_id) for note in notes)
        payload={'draft':proposal(json.dumps({'verified_sources':[one,two]})),'status':'draft'}
        old=client.post('/api/ideas',json=payload)
        assert old.status_code==201,old.text
        original_sources=old.json()['sources']
        original_blocks=client.get('/api/documents/'+second).json()['evidence']
        event=merge(client,left,right)
        available=client.get('/api/idea-sources').json()
        assert any(value['note_id']==two['note_id'] for value in available)
        created=client.post('/api/ideas',json=payload)
        assert created.status_code==201,created.text
        new_idea=created.json()
        exported=client.post('/api/exports',json={'idea_ids':[new_idea['id']]}).json()
        exported_bytes=client.get(exported['markdown_url']).content
        assert '当前汇总主条目ID'.encode() in exported_bytes
        note=notes[1]
        edited=client.post(f'/api/documents/{second}/notes',json={'base_note_id':str(note.id),'changes':{str(note.items[0].id):'Later manual note, preserve on undo'}})
        assert edited.status_code==201,edited.text
        client.put(f'/api/papers/{left}/visibility?removed=true')
        assert client.get('/api/idea-sources').json()==[]
        assert client.post(f"/api/merges/{event['id']}/undo").status_code==200
        for idea_id, before_sources in [(old.json()['id'], original_sources), (new_idea['id'], new_idea['sources'])]:
            after_sources=client.get('/api/ideas/'+idea_id).json()['sources']
            # Visibility follows the later root removal; all original source/version fields stay intact.
            assert [{k:v for k,v in value.items() if k!='removed'} for value in after_sources]==[{k:v for k,v in value.items() if k!='removed'} for value in before_sources]
            assert all(value['removed']==(value['paper_id']==left) for value in after_sources)
        assert str(list_notes(settings,second)[0].id)==edited.json()['id']
        assert client.get('/api/documents/'+second).json()['evidence']==original_blocks
        assert client.get(exported['markdown_url']).content==exported_bytes
        with closing(db.connect(settings.db_path)) as conn:
            assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
            assert conn.execute('SELECT paper_id FROM documents WHERE id=?',(second,)).fetchone()[0]==right
            audit=json.loads(conn.execute('SELECT before_snapshot FROM merge_events WHERE id=?',(event['id'],)).fetchone()[0])
            assert any(value['id']==str(notes[1].id) for value in audit['origins']['notes'])


def test_conflicting_edits_and_merge_order_are_not_blindly_rolled_back(tmp_path):
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        docs=[upload(client,'Fixture '+name,title=name)[0] for name in ('A','B','C')]
        a,b,c=(doc['paper_id'] for doc in docs)
        first=merge(client,a,b,{'title':'right'})
        client.patch('/api/papers/'+a,json={'title':'Later human edit'})
        rejected=client.post(f"/api/merges/{first['id']}/undo")
        assert rejected.status_code==409 and 'title' in rejected.text
        assert next(p for p in client.get('/api/papers').json() if p['id']==a)['title']=='Later human edit'
        history=client.get(f'/api/papers/{a}/merges').json()[0]
        assert 'title' in history['undo_conflict']
        client.patch('/api/papers/'+a,json={'title':'B'})  # User explicitly resolves the conflict.
        chain=compare(client,c,a)
        assert client.post('/api/merges',json=body(chain,fields={key:'left' for key in db.PAPER_EDITABLE})).status_code==409
        second=merge(client,a,c,{'title':'right'})
        assert client.post(f"/api/merges/{first['id']}/undo").status_code==409
        assert client.post(f"/api/merges/{second['id']}/undo").status_code==200
        assert client.post(f"/api/merges/{first['id']}/undo").status_code==200
        assert {p['title'] for p in client.get('/api/papers').json()}=={'A','B','C'}


@pytest.mark.parametrize('operation',['merge','undo'])
def test_transaction_failure_leaves_metadata_and_group_mapping_unchanged(tmp_path,operation):
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        a,_=upload(client,'A fixture',title='A')
        b,_=upload(client,'B fixture',title='B')
        left,right=a['paper_id'],b['paper_id']
        event=merge(client,left,right,{'title':'right'}) if operation=='undo' else None
        expected=client.get('/api/papers').json()
        with db.transaction(settings.db_path) as conn:
            trigger='BEFORE UPDATE OF status' if operation=='undo' else 'BEFORE INSERT'
            conn.execute(f"CREATE TRIGGER controlled_abort {trigger} ON merge_events BEGIN SELECT RAISE(ABORT,'controlled transaction failure'); END")
        with pytest.raises(sqlite3.IntegrityError,match='controlled transaction failure'):
            if event:client.post(f"/api/merges/{event['id']}/undo")
            else:merge(client,left,right,{'title':'right'})
        assert client.get('/api/papers').json()==expected


def test_v6_migration_keeps_unknown_publication_metadata_and_source_ownership(tmp_path,monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(db,'MIGRATIONS',db.MIGRATIONS[:6])
        db.initialize(tmp_path/'legacy.sqlite3')
        paper=db.create_paper(tmp_path/'legacy.sqlite3',Paper(title='Existing v6'))
    db.initialize(tmp_path/'legacy.sqlite3')
    with closing(db.connect(tmp_path/'legacy.sqlite3')) as conn:
        row=conn.execute('SELECT doi,year FROM papers WHERE id=?',(str(paper.id),)).fetchone()
        assert row['doi']=='' and row['year'] is None
        assert conn.execute('SELECT library_id FROM paper_owners WHERE original_id=?',(str(paper.id),)).fetchone()[0]==str(paper.id)
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
