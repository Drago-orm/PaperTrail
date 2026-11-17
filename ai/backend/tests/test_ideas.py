"""Self-authored, controlled proposals test provenance and plans, not scientific novelty."""
from contextlib import closing
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import db
from app.agents import ideas as agent
from app.agents.client import Completion
from app.agents.client import ModelError
from app.config import Settings
from app.main import create_app
from app.schemas import MinimumExperiment
from app.services.reading_runs import list_notes
from app.services import workflow
from test_reading import upload, wait_run
from test_workflow import transports, start
from test_pdf_import import make_pdf


def plan():
    common = {'model':'small linear predictor','dataset':'self-authored ToySet','split':'fixed train/test seed 42'}
    return {'control':{**common,'settings':{'mask_ratio':'0','seed':'42','train_steps':'100'}},
            'treatment':{**common,'settings':{'mask_ratio':'0.15','seed':'42','train_steps':'100'}},
            'changed_factor':'mask_ratio','metric':'test mean squared error',
            'evaluation':'Repeat 3 seeds with identical train/test split and compare held-out error.',
            'pending_confirmations':['Confirm data availability and CPU time; this is a proposed experiment, not a reported result.']}


def proposal(user):
    data=json.loads(user)
    return {'question':'Does masking one input factor change prediction error?',
        'hypothesis':'Pending validation: masking may change error; improvement is not guaranteed.',
        'derivation':'Controlled example: derive a testable question from the supplied supported source.',
        'difference':'Propose a new masking setting; do not claim this is an original contribution.',
        'resources':'A local CPU and self-authored synthetic data, availability to confirm.',
        'failure_conditions':'No consistent held-out difference across seeds refutes the improvement hypothesis.',
        'novelty_checks':['Which published studies already test this masking setting?'],
        'experiment':plan(),'sources':[{'note_id':value['note_id'],'item_id':value['item_id']} for value in data['verified_sources']]}


def setup(settings, monkeypatch):
    calls=transports(monkeypatch)
    idea_calls=[]
    def controlled(settings, system, user):
        idea_calls.append(json.loads(user))
        return Completion(json.dumps(proposal(user)),{'total_tokens':17},1)
    monkeypatch.setattr(agent,'complete',controlled)
    return calls,idea_calls


def supported(client,settings,content=None):
    did=upload(client,content)
    result=wait_run(client,start(client,did)['id'])
    assert result['status']=='succeeded',result
    note=list_notes(settings,did)[0]
    item=next(value for value in note.items if value.verification_status=='supported')
    return did,{'note_id':str(note.id),'item_id':str(item.id)}


def generate(client,choices,**conditions):
    response=client.post('/api/idea-runs',json={'sources':choices,**conditions})
    assert response.status_code==202,response.text
    return wait_run(client,response.json()['id'])


def draft_of(view):
    fields=('question','hypothesis','derivation','difference','resources','failure_conditions','novelty_checks','experiment')
    draft={key:view[key] for key in fields}
    draft['sources']=[{'note_id':value['note_id'],'item_id':value['item_id']} for value in view['sources']]
    draft['sources']=list({(value['note_id'],value['item_id']):value for value in draft['sources']}.values())
    return draft


@pytest.mark.parametrize('defect',['model','dataset','split','two_factors','same_value','extra_factor'])
def test_experiment_is_concrete_same_data_model_one_factor(defect):
    value=plan()
    if defect in {'model','dataset','split'}:
        value['treatment'][defect]='different'
    elif defect=='two_factors':value['treatment']['settings']['seed']='43'
    elif defect=='same_value':value['treatment']['settings']['mask_ratio']='0'
    else:value['treatment']['settings']['optimizer']='Adam'
    with pytest.raises(ValueError):MinimumExperiment.model_validate(value)
    good=MinimumExperiment.model_validate(plan())
    assert good.control.model==good.treatment.model and 'mask_ratio' in good.summary()


def test_multisource_versions_removed_paper_and_manual_cache_invalidation(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','never-log-secret',data_dir=tmp_path)
    _,calls=setup(settings,monkeypatch)
    with TestClient(create_app(settings)) as client:
        first,one=supported(client,settings)
        second,two=supported(client,settings,make_pdf([[(40,750,'Different self-authored source'),(40,710,'A second source of evidence.')]]))
        run=generate(client,[one,two],interest='masking',resources='CPU')
        assert run['status']=='succeeded',run
        view=client.get('/api/ideas/'+run['output']['idea_id']).json()
        assert len(view['sources'])==2 and len({value['document_id'] for value in view['sources']})==2
        assert all(value['note_version']==3 and value['content'] for value in view['sources'])
        cached=generate(client,[one,two],interest='masking',resources='CPU')
        assert cached['output']['cached'] and cached['output']['api_calls']==0 and len(calls)==1
        draft=draft_of(view);draft['question']='Human edited research question'
        saved=client.post('/api/ideas',json={'base_idea_id':view['id'],'draft':draft,'status':'confirmed'})
        assert saved.status_code==201,saved.text
        manual=saved.json()
        assert manual['version']==2 and manual['is_user_edited']
        assert client.post('/api/ideas',json={'base_idea_id':view['id'],'draft':draft,'status':'testing'}).status_code==409
        regenerated=generate(client,[one,two],interest='masking',resources='CPU')
        assert regenerated['status']=='succeeded' and not regenerated['output']['cached']
        assert len(calls)==2 and regenerated['output']['idea_id']!=manual['id']
        assert client.get('/api/ideas/'+manual['id']).json()==manual
        assert len(client.get('/api/ideas/'+manual['id']+'/versions').json())==2
        assert len(client.get('/api/ideas?status=confirmed&q=Human').json())==1
        paper=manual['sources'][0]['paper_id']
        assert client.put('/api/papers/'+paper+'/visibility?removed=true').status_code==200
        after=client.get('/api/ideas/'+manual['id']).json()
        assert after['sources'][0]['removed'] and after['sources'][0]['note_version']==manual['sources'][0]['note_version']
        assert client.get('/api/documents/'+first+'/file').status_code==200
        for conditions in [{'interest':'different','resources':'CPU'},{'interest':'masking','resources':'GPU'}]:
            assert generate(client,[one,two],**conditions)['status']=='succeeded'
        assert len(calls)==4
        source_note=list_notes(settings,second)[0]
        assert client.post(f'/api/documents/{second}/notes',json={'base_note_id':str(source_note.id),
            'changes':{str(source_note.items[0].id):'Human changes a different note item'}}).status_code==201
        assert not generate(client,[one,two],interest='masking',resources='CPU')['output']['cached']
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/ideas/'+manual['id']).json()['question']=='Human edited research question'


@pytest.mark.parametrize('defect',['source','two_changes','claim_confidence'])
def test_bad_model_idea_is_not_saved(tmp_path,monkeypatch,defect):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    setup(settings,monkeypatch)
    def bad(settings,facts,evidence,conditions):
        value=proposal(json.dumps({'verified_sources':facts}))
        if defect=='source':value['sources'][0]['item_id']=str(uuid4())
        elif defect=='two_changes':value['experiment']['treatment']['settings']['seed']='99'
        else:value['confidence']=.99
        return Completion(json.dumps(value),None,1)
    monkeypatch.setattr(agent,'generate',bad)
    with TestClient(create_app(settings)) as client:
        _,choice=supported(client,settings)
        run=generate(client,[choice])
        assert run['status']=='failed' and run['error']
        assert client.get('/api/ideas').json()==[]


def test_four_roles_and_no_supported_source_fail_explicitly(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    _,calls=setup(settings,monkeypatch)
    with TestClient(create_app(settings)) as client:
        did=upload(client)
        response=client.post(f'/api/documents/{did}/workflow-runs',json={'revise':False,'interest':'masking','resources':'CPU'})
        run=wait_run(client,response.json()['id'])
        assert run['status']=='succeeded',run
        assert run['input_scope']['include_idea'] and run['input_scope']['max_api_calls']==26
        assert set(run['output']['nodes'])=={'reading','writing','verification','idea'}
        assert run['output']['idea_id'] and len(calls)==1
        assert client.get('/api/ideas/'+run['output']['idea_id']).json()['sources']
        note=list_notes(settings,did)[0]
        unchecked=next(value for value in note.items if value.verification_status=='not_reported')
        response=client.post('/api/idea-runs',json={'sources':[{'note_id':str(note.id),'item_id':str(unchecked.id)}]})
        assert response.status_code==409 and '已支持' in response.text
        from app.agents import verification
        def no_support(settings,system,user):
            payload=json.loads(user)
            return Completion(json.dumps({'items':[{'item_id':value['id'],'status':'not_reported',
                'reason':'受控缺失范围，不能生成事实来源','evidence_ids':[],'quotes':[]} for value in payload['claims']]}),None,1)
        monkeypatch.setattr(verification,'complete',no_support)
        other=upload(client,make_pdf([[(40,750,'No supported scientific source fixture')]]))
        response=client.post(f'/api/documents/{other}/workflow-runs',json={'revise':False})
        failed=wait_run(client,response.json()['id'])
        assert failed['status']=='failed' and '没有已支持' in failed['error']
        assert failed['output']['nodes']['verification']['status']=='succeeded' and len(calls)==1


def test_manual_create_and_migration_preserve_legacy_source_limitations(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    setup(settings,monkeypatch)
    with TestClient(create_app(settings)) as client:
        _,choice=supported(client,settings)
        value=proposal(json.dumps({'verified_sources':[choice]}))
        created=client.post('/api/ideas',json={'draft':value,'status':'draft'})
        assert created.status_code==201 and created.json()['is_user_edited']
    legacy=tmp_path/'legacy.sqlite3'
    with monkeypatch.context() as old:
        old.setattr(db,'MIGRATIONS',db.MIGRATIONS[:5]);db.initialize(legacy)
    uid=str(uuid4())
    with db.transaction(legacy) as conn:
        conn.execute('INSERT INTO ideas VALUES (?,?,?,?,?,?,?,?,?,?,?)',(uid,'Legacy','Hypothesis','','','free text plan','','','[]','draft','2026-11-18T00:00:00Z'))
    db.initialize(legacy)
    with closing(db.connect(legacy)) as conn:
        row=conn.execute('SELECT * FROM ideas WHERE id=?',(uid,)).fetchone()
        assert row['root_id']==uid and row['version']==1 and row['experiment_json'] is None
        assert row['minimum_experiment']=='free text plan'


def test_idea_failure_retries_only_idea_and_child_commit_is_reused(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    calls,idea_calls=setup(settings,monkeypatch)
    original=agent.complete
    def failed(*args):raise ModelError('受控Idea失败',calls=2)
    monkeypatch.setattr(agent,'complete',failed)
    with TestClient(create_app(settings)) as client:
        did=upload(client)
        response=client.post(f'/api/documents/{did}/workflow-runs',json={'revise':False})
        first=wait_run(client,response.json()['id'])
        assert first['status']=='failed' and first['output']['nodes']['verification']['status']=='succeeded'
        assert len(list_notes(settings,did))==3
        monkeypatch.setattr(agent,'complete',original)
        assert client.post('/api/workflow-runs/'+first['id']+'/retry').status_code==202
        final=wait_run(client,first['id'])
        assert final['status']=='succeeded',final
        assert calls=={'reading':1,'writing':1,'verification':2} and len(idea_calls)==1
        assert all(final['output']['nodes'][role]['cached'] for role in ('reading','writing','verification'))
    # Controlled stop after the Idea child commits, before its parent records the result.
    second_dir=tmp_path/'interrupted'
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=second_dir)
    _,idea_calls=setup(settings,monkeypatch)
    account=workflow.account
    def stop(output,reply,**kwargs):
        if output.get('nodes',{}).get('idea',{}).get('status')=='running':raise SystemExit('controlled stop')
        return account(output,reply,**kwargs)
    monkeypatch.setattr(workflow,'account',stop)
    with TestClient(create_app(settings)) as client:
        did=upload(client)
        queued=client.post(f'/api/documents/{did}/workflow-runs',json={'revise':False}).json()
    monkeypatch.setattr(workflow,'account',account)
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/workflow-runs/'+queued['id']+'/retry').status_code==202
        result=wait_run(client,queued['id'])
        assert result['status']=='succeeded',result
        assert len(idea_calls)==1 and len(client.get('/api/ideas').json())==1
        assert result['output']['nodes']['idea']['cached']


def test_source_changes_and_missing_configuration_are_explicit(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    setup(settings,monkeypatch)
    with TestClient(create_app(settings)) as client:
        did,choice=supported(client,settings)
        with db.transaction(settings.db_path) as conn:
            row=conn.execute('SELECT items_json FROM notes WHERE id=?',(choice['note_id'],)).fetchone()
            original=json.loads(row['items_json']);changed=[dict(value) for value in original]
            next(value for value in changed if value['id']==choice['item_id'])['content_type']='research_idea'
            conn.execute('UPDATE notes SET items_json=? WHERE id=?',(json.dumps(changed),choice['note_id']))
        rejected=client.post('/api/idea-runs',json={'sources':[choice]})
        assert rejected.status_code==409 and '来源事实' in rejected.text
        with db.transaction(settings.db_path) as conn:
            conn.execute('UPDATE notes SET items_json=? WHERE id=?',(json.dumps(original),choice['note_id']))
            conn.execute('UPDATE documents SET parse_version=? WHERE id=?',('changed',did))
        response=client.post('/api/idea-runs',json={'sources':[choice]})
        assert response.status_code==409 and '解析版本' in response.text
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response=client.post('/api/idea-runs',json={'sources':[choice]})
        assert response.status_code==503 and '配置' in response.text
