"""CC0 self-authored PDFs and literal saved notes; no scientific or model-quality claims."""
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app import db
from app.config import Settings
from app.main import create_app
from app.schemas import AnalysisItem, EvidenceQuote, Note
from app.services.comparisons import MATRIX
from test_pdf_import import make_pdf


def labels(**changes):
    values={'Task':'toy classification','Dataset':'SelfAuthoredSet','Data split':'fixed test A','Metric':'accuracy percent',
            'Evaluation protocol':'evaluate the same 20 held-out samples','Training configuration':'seed42 steps100',
            'Inference configuration':'batch1 temperature0','Code':'not provided','Data availability':'self-authored fixture data',
            'Dependencies':'Python 3.14','Parameters':'lr0.01'}
    values.update(changes)
    return [f'{key}: {value}' for key,value in values.items()]


def study(client,settings,index,lines=None):
    lines=labels() if lines is None else lines
    content=make_pdf([[(40,750-24*i,line) for i,line in enumerate(lines)],
                      [(40,750,f'Controlled evidence for study {index}; no scientific result is claimed.'),
                       (40,710,'We used one A100 GPU as a self-authored example.')]])
    response=client.post('/api/documents',files={'file':('self-authored.pdf',content)})
    assert response.status_code==200,response.text
    parsed=response.json();doc=parsed['document']
    assert parsed['report']['status']!='failed',parsed['report']
    client.patch('/api/papers/'+doc['paper_id'],json={'title':f'自制比较样例 {index}','authors':['自制作者']})
    block=next(b for b in parsed['evidence'] if f'Controlled evidence for study {index}' in b['text'])
    note=Note(paper_id=doc['paper_id'],document_id=doc['id'],source_mode='unknown',items=[
        AnalysisItem(field=field,content_type='system_analysis',content=f'自制笔记 {field} {index}',
                     evidence_ids=[block['id']],quotes=[EvidenceQuote(evidence_id=block['id'],text=block['text'])],
                     verification_reason='受控自制笔记，未进行真实模型语义核查') for field in MATRIX if field!='ablation']+
        [AnalysisItem(field='ablation',content_type='author_statement',content='未报告',verification_status='not_reported')])
    db.create_note(settings.db_path,note)
    return note,parsed,content


def create(client,*notes,annotations=None):
    response=client.post('/api/study-comparisons',json={'studies':[{'note_id':str(note.id),'annotations':(annotations or {}).get(str(note.id),[])} for note in notes]})
    assert response.status_code==201,response.text
    return response.json()


def test_matrix_sources_conditions_missing_resources_and_no_model_calls(tmp_path,monkeypatch):
    from app.agents import reading,writing,verification,ideas
    def forbidden(*args,**kwargs):pytest.fail('Comparison called a model')
    for agent in (reading,writing,verification,ideas):monkeypatch.setattr(agent,'complete',forbidden)
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        one,first,content=study(client,settings,1)
        two,second,_=study(client,settings,2)
        report=create(client,one,two)
        assert report['pairs'][0]['status']=='text_match'
        assert '不等于已经可比' in report['pairs'][0]['warning']
        assert '不计算成功率' in report['warning']
        for saved in report['studies']:
            assert saved['source_mode']=='unknown' and saved['verification_run_id'] is None
            assert any('未保存核查任务' in warning for warning in saved['warnings'])
            originals={block['id']:block for block in saved['evidence']}
            for field,cell in saved['cells'].items():
                if field=='ablation':assert cell['items']==[] and cell['warnings'];continue
                assert cell['items']
                for item in cell['items']:
                    assert item['verification_status']=='unchecked' and item['quotes']
                    for quote in item['quotes']:
                        source=originals[quote['evidence_id']]
                        assert quote['text'] in source['text'] and source['document_id']==saved['document_id']
                        assert source['physical_page']==2 and 0<=source['bbox'][0]<source['bbox'][2]<=source['page_width']
            assert saved['readiness']['code']['status']=='not_provided' and saved['readiness']['code']['value'] is None
            assert saved['readiness']['code']['entries']  # Explicit absence also has a real source.
            assert saved['readiness']['hardware']['status']=='not_reported' and saved['readiness']['hardware']['value'] is None
            assert saved['readiness']['hardware']['entries']==[]
        assert report['studies'][0]['sha256']==sha256(content).hexdigest()
        assert report['studies'][0]['document_id']==first['document']['id']
        assert report['studies'][1]['document_id']==second['document']['id']


def test_different_protocol_split_units_unknown_and_ambiguous_are_never_ranked(tmp_path):
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        a,_,_=study(client,settings,1)
        b,_,_=study(client,settings,2,labels(**{'Data split':'random test B','Evaluation protocol':'different held-out protocol','Metric':'accuracy fraction'}))
        c,_,_=study(client,settings,3,['Code: unavailable'])
        report=create(client,a,b,c)
        pair=report['pairs'][0]
        assert pair['status']=='different'
        assert {c['field'] for c in pair['checks'] if c['status']=='different'}=={'split','metric','protocol'}
        assert report['pairs'][1]['status']=='unknown' and report['pairs'][2]['status']=='unknown'
        d,_,_=study(client,settings,4,labels()+['Data split: second experiment split'])
        ambiguous=create(client,a,d)
        assert ambiguous['studies'][1]['conditions']['split']['status']=='ambiguous'
        assert ambiguous['studies'][1]['conditions']['split']['value'] is None
        assert len(ambiguous['studies'][1]['conditions']['split']['entries'])==2
        assert ambiguous['pairs'][0]['status']=='unknown'
        # Case-sensitive units must not collapse into an apparent match (milli vs mega).
        e,_,_=study(client,settings,5,labels(**{'Metric':'energy mJ'}))
        f,_,_=study(client,settings,6,labels(**{'Metric':'energy MJ'}))
        units=create(client,e,f)
        assert units['pairs'][0]['status']=='different'
        assert next(check for check in units['pairs'][0]['checks'] if check['field']=='metric')['status']=='different'
        assert all(key not in report for key in ('ranking','winner','success_rate','readiness_score'))


def test_manual_excerpt_requires_exact_local_evidence_and_does_not_override_other_settings(tmp_path):
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        a,parsed,_=study(client,settings,1)
        b,other,_=study(client,settings,2,labels(**{'Code':'https://example.invalid/none/tool'}))
        hardware=next(block for block in parsed['evidence'] if 'one A100 GPU' in block['text'])
        annotation={'field':'hardware','evidence_id':hardware['id'],'text':hardware['text'],'value':'one A100 GPU'}
        report=create(client,a,b,annotations={str(a.id):[annotation]})
        check=report['studies'][0]['readiness']['hardware']
        assert check['status']=='clue' and check['value']=='one A100 GPU' and check['entries'][0]['origin']=='manual'
        assert report['studies'][1]['readiness']['code']['status']=='clue'  # URL path "none" is not absence.
        for bad,status in [({**annotation,'value':'invented GPU'},422),({**annotation,'field':'success_rate'},422),
                           ({**annotation,'text':'invented one A100 GPU quote'},409),
                           ({**annotation,'evidence_id':other['evidence'][0]['id']},409)]:
            response=client.post('/api/study-comparisons',json={'studies':[{'note_id':str(a.id),'annotations':[bad]},{'note_id':str(b.id)}]})
            assert response.status_code==status,response.text
        conflict={**annotation,'field':'split','text':hardware['text'],'value':'A100'}
        limited=create(client,a,b,annotations={str(a.id):[conflict]})
        assert limited['studies'][0]['conditions']['split']['status']=='ambiguous'  # Manual supplements, never erases known settings.


def test_bounds_duplicate_versions_merged_groups_and_missing_notes(tmp_path):
    from test_dedup import merge
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        notes=[study(client,settings,i)[0] for i in range(5)]
        assert len(create(client,*notes)['studies'])==5
        for values in ([],[notes[0]],[notes[0],notes[0]],notes+[notes[0]]):
            assert client.post('/api/study-comparisons',json={'studies':[{'note_id':str(n.id)} for n in values]}).status_code==422
        assert client.post('/api/study-comparisons',json={'studies':[{'note_id':str(notes[0].id)},{'note_id':str(uuid4())}]}).status_code==404
        newer=client.post(f'/api/documents/{notes[0].document_id}/notes',json={'base_note_id':str(notes[0].id),'changes':{str(notes[0].items[0].id):'新人工版本'}}).json()
        assert client.post('/api/study-comparisons',json={'studies':[{'note_id':str(notes[0].id)},{'note_id':newer['id']}]}).status_code==409
        view=client.get('/api/paper-comparison',params={'left_id':str(notes[0].paper_id),'right_id':str(notes[1].paper_id)}).json()
        client.put('/api/paper-relations',json={'left_id':str(notes[0].paper_id),'right_id':str(notes[1].paper_id),'token':view['token'],'kind':'version'})
        assert create(client,notes[0],notes[1])['pairs'][0]['relations'][0]['kind']=='version'
        merge(client,str(notes[0].paper_id),str(notes[1].paper_id))
        assert client.post('/api/study-comparisons',json={'studies':[{'note_id':str(n.id)} for n in notes[:2]]}).status_code==409


def test_snapshot_keeps_exact_versions_after_edits_restart_and_detects_corruption(tmp_path):
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        a,_,_=study(client,settings,1);b,parsed,_=study(client,settings,2)
        # Invalid saved text is explicitly excluded instead of receiving a fabricated citation.
        invalid=a.items[0].model_copy(update={'quotes':[EvidenceQuote(evidence_id=a.items[0].evidence_ids[0],text='not in saved original')]})
        with db.transaction(settings.db_path) as conn:
            conn.execute('UPDATE notes SET items_json=? WHERE id=?',(json.dumps([invalid.model_dump(mode='json'),*[i.model_dump(mode='json') for i in a.items[1:]]]),str(a.id)))
        missing=tmp_path/parsed['document']['storage_path'];missing.unlink()
        saved=create(client,a,b)
        assert saved['studies'][0]['cells']['goal']['items']==[] and saved['studies'][0]['cells']['goal']['warnings']
        assert not saved['studies'][1]['file_available'] and any('PDF缺失' in w for w in saved['studies'][1]['warnings'])
        client.patch('/api/papers/'+str(a.paper_id),json={'title':'后来的题目'})
        client.post(f'/api/documents/{a.document_id}/notes',json={'base_note_id':str(a.id),'changes':{str(a.items[1].id):'后来的人工方法'}})
        assert client.get('/api/study-comparisons/'+saved['id']).json()==saved
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/study-comparisons/'+saved['id']).json()==saved
        path=tmp_path/'comparisons'/f"{saved['id']}.json"
        broken={**saved,'warning':'changed without updating checksum'}
        path.write_text(json.dumps(broken),encoding='utf-8')
        result=client.get('/api/study-comparisons/'+saved['id'])
        assert result.status_code==409 and '完整性' in result.text
        assert client.get('/api/study-comparisons/'+str(uuid4())).status_code==404


def test_truncated_neighbour_does_not_become_complete_configuration_and_write_failure_is_real(tmp_path,monkeypatch):
    from app.services import retrieval
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        a,_,_=study(client,settings,1,['Hardware: '+'CPU '*12])
        b,_,_=study(client,settings,2,['Hardware: '+'CPU '*12])
        with db.transaction(settings.db_path) as conn:
            conn.execute("UPDATE notes SET items_json='[]'")
        monkeypatch.setattr(retrieval,'MAX_CHARS',32)
        limited=create(client,a,b)
        for value in limited['studies']:
            assert value['scope']['truncated_evidence_ids']
            assert value['readiness']['hardware']['value'] is None
            assert any('截短' in warning for warning in value['warnings'])
        def fail_replace(*args,**kwargs):raise OSError('controlled full disk')
        monkeypatch.setattr(Path,'replace',fail_replace)
        response=client.post('/api/study-comparisons',json={'studies':[{'note_id':str(a.id)},{'note_id':str(b.id)}]})
        assert response.status_code==500 and '无法读写' in response.text
        assert list((tmp_path/'comparisons').glob('*.tmp'))==[]
        assert len(list((tmp_path/'comparisons').glob('*.json')))==1
