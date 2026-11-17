"""Controlled translations exercise alignment/protection, not linguistic quality."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.agents import translation as agent
from app.agents.client import Completion, ModelError
from app.config import Settings
from app.main import create_app
from app.schemas import EvidenceBlock
from app.services import translation
from test_pdf_import import make_pdf


def upload(client,lines=None):
    lines=lines or ['1 Introduction','self-attention 95.2% seed 42 [1].',
                    'Use $lr=0.01$ and $x^2$.','2 Other section','An unrelated paragraph.']
    content=make_pdf([[(40,750-30*i,text) for i,text in enumerate(lines)],[(40,750,'Text on a separate page.')]])
    response=client.post('/api/documents',files={'file':('self-authored.pdf',content)})
    assert response.status_code==200,response.text
    parsed=response.json()
    assert parsed['report']['status']!='failed',parsed['report']
    assert parsed['report']['extracted_pages']==[1,2],parsed['report']
    return parsed,content


def controlled(monkeypatch):
    calls=[]
    def complete(settings,system,user):
        data=json.loads(user);calls.append(data)
        return Completion(json.dumps({'translations':[{'evidence_id':value['evidence_id'],'text':'受控译文：'+value['text']} for value in reversed(data['blocks'])]}),{'total_tokens':19},1)
    monkeypatch.setattr(agent,'complete',complete)
    return calls


def prepare(client,data,**extra):
    block=next(b for b in data['evidence'] if 'self-attention' in b['text'])
    body={'document_id':data['document']['id'],'evidence_id':block['id'],**extra}
    response=client.post('/api/translations/preview',json=body)
    assert response.status_code==200,response.text
    return body,response.json()


def execute(client,body,preview):
    response=client.post('/api/translations',json={**body,'confirmation_token':preview['confirmation_token']})
    assert response.status_code==200,response.text
    return response.json()


def test_confirmation_literal_terms_alignment_cache_and_restart(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','never-log-secret',data_dir=tmp_path)
    calls=controlled(monkeypatch)
    with TestClient(create_app(settings)) as client:
        data,content=upload(client)
        body,preview=prepare(client,data,glossary=[{'source':'self-attention','target':'自注意力'}])
        assert calls==[] and preview['configuration']['service']=='https://controlled.invalid'
        assert preview['blocks'][0]['will_send'] and '不上传PDF' in preview['warning']
        assert client.post('/api/translations',json=body).status_code==422
        assert client.post('/api/translations',json={**body,'confirmation_token':'0'*64}).status_code==409
        assert calls==[]
        result=execute(client,body,preview)
        value=result['results'][0]
        assert result['api_calls']==1 and result['usage']=={'total_tokens':19}
        assert value['evidence_id']==body['evidence_id'] and value['document_id']==data['document']['id'] and value['physical_page']==1
        assert '自注意力' in value['translation'] and '95.2%' in value['translation'] and '42' in value['translation'] and '[1]' in value['translation']
        assert '[[PT_' not in value['translation'] and value['terms']==['self-attention']
        assert '95.2%' not in calls[0]['blocks'][0]['text']  # Sent as immutable markers, not editable numerals.
        _,hit=prepare(client,data,glossary=body['glossary'])
        assert hit['blocks'][0]['cached'] and not hit['blocks'][0]['will_send']
        cached=execute(client,body,hit)
        assert cached['api_calls']==0 and cached['usage'] is None and cached['results'][0]['cached'] and len(calls)==1
        assert cached['results'][0]['translation']==value['translation']
        assert client.get('/api/documents/'+data['document']['id']+'/file').content==content
        assert all('never-log-secret' not in path.read_text(encoding='utf-8') for path in (tmp_path/'translations').glob('*.json'))
    with TestClient(create_app(settings)) as client:
        _,hit=prepare(client,data,glossary=body['glossary'])
        assert execute(client,body,hit)['api_calls']==0 and len(calls)==1


@pytest.mark.parametrize('defect',['missing','extra_number','foreign_id','duplicate_id','reordered','cross_block'])
def test_invalid_model_output_never_becomes_cached_success(tmp_path,monkeypatch,defect):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    def bad(settings,system,user):
        blocks=json.loads(user)['blocks']
        items=[{'evidence_id':b['evidence_id'],'text':'受控：'+b['text']} for b in blocks]
        tokens=agent.TOKEN.findall(items[0]['text'])
        if defect=='missing':items[0]['text']=items[0]['text'].replace(tokens[0],'')
        elif defect=='extra_number':items[0]['text']+='新增95.8%'
        elif defect=='foreign_id':items[0]['evidence_id']=str(uuid4())
        elif defect=='duplicate_id':items.append(dict(items[0]))
        elif defect=='reordered':items[0]['text']=' '.join(reversed(tokens))
        else:items[0]['text'],items[1]['text']=items[1]['text'],items[0]['text']
        return Completion(json.dumps({'translations':items}),{'total_tokens':7},1)
    monkeypatch.setattr(agent,'complete',bad)
    with TestClient(create_app(settings)) as client:
        data,content=upload(client)
        body,preview=prepare(client,data,scope='section')
        failed=client.post('/api/translations',json={**body,'confirmation_token':preview['confirmation_token']})
        assert failed.status_code==503 and '校验失败' in failed.text and '已知请求1次' in failed.text
        assert list((tmp_path/'translations').glob('*.json'))==[]
        assert client.get('/api/documents/'+data['document']['id']+'/file').content==content


def test_local_formula_code_glossary_and_chinese_adjacent_numbers(monkeypatch,tmp_path):
    def forbidden(*args,**kwargs):pytest.fail('Local preservation called a model')
    monkeypatch.setattr(agent,'complete',forbidden)
    settings=Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        data,_=upload(client,['1 Code','x = 0.95 + 2','self-attention'])
        formula=next(b for b in data['evidence'] if b['text'].startswith('x ='))
        body={'document_id':data['document']['id'],'evidence_id':formula['id']}
        preview=client.post('/api/translations/preview',json=body).json()
        assert not preview['configuration']['configured'] and not preview['blocks'][0]['will_send']
        result=execute(client,body,preview)
        assert result['api_calls']==0 and result['results'][0]['status']=='preserved' and result['results'][0]['translation']==formula['text']
        body,preview=prepare(client,data,glossary=[{'source':'self-attention','target':'自注意力'}])
        local=execute(client,body,preview)
        assert local['api_calls']==0 and local['results'][0]['status']=='glossary' and local['results'][0]['translation']=='自注意力'
    # Literal protection does not depend on PDF font support for Chinese text.
    block=EvidenceBlock(document_id=uuid4(),physical_page=1,paragraph_index=0,text='准确率95.2％，模型A100，能耗1.5 mJ（张三，2024）。',bbox=(0,0,100,20),page_width=200,page_height=300)
    protected=agent.protect(block,[])
    assert '95.2' not in protected.masked and 'A100' not in protected.masked and '2024' not in protected.masked
    assert protected.restore(protected.masked)==block.text
    with pytest.raises(ValueError):protected.restore(protected.masked+'新增数值88')
    code=block.model_copy(update={'text':'Use `lr=0.01` and $x^2$ with (Smith, 2024).'})
    prepared=agent.protect(code,[])
    assert prepared.local_status is None and prepared.restore(prepared.masked)==code.text
    assert agent.protect(block.model_copy(update={'text':'α ∈ R'}),[]).local_status=='preserved'
    for source,target in [('5 ms','5 s'),('accuracy','准确率99%'),('GPT-4','GPT-5'),('term','x=2')]:
        with pytest.raises(ValueError):agent.Term(source=source,target=target)


def test_section_is_page_contiguous_bounded_and_reuses_paragraph_cache(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    calls=controlled(monkeypatch)
    with TestClient(create_app(settings)) as client:
        data,_=upload(client)
        one,preview=prepare(client,data)
        execute(client,one,preview)
        section,preview=prepare(client,data,scope='section')
        assert len(preview['blocks'])==3 and all(b['physical_page']==1 for b in preview['blocks'])
        assert sum(b['cached'] for b in preview['blocks'])==1
        result=execute(client,section,preview)
        assert result['api_calls']==1 and len(calls)==2
        assert len(calls[-1]['blocks'])==1  # Heading preserved locally, first paragraph already cached.
        literal=next(r for r in result['results'] if '$lr' in r['source'])
        assert '$lr=0.01$' in literal['translation'] and '$x^2$' in literal['translation']
        no_section=data['evidence'][-1]
        assert client.post('/api/translations/preview',json={**one,'evidence_id':no_section['id'],'scope':'section'}).status_code==409
        many,_=upload(client,['1 Long section']+[f'Ordinary text part {chr(65+i)}.' for i in range(7)])
        assert client.post('/api/translations/preview',json={'document_id':many['document']['id'],'evidence_id':many['evidence'][0]['id'],'scope':'section'}).status_code==409
        assert client.post('/api/translations/preview',json={**one,'evidence_id':many['evidence'][0]['id']}).status_code==404
        assert len(calls)==2


def test_cache_keys_confirmed_configuration_failures_and_lock(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    calls=controlled(monkeypatch)
    with TestClient(create_app(settings)) as client:
        data,_=upload(client)
        body,preview=prepare(client,data)
        with translation.LOCK:
            assert client.post('/api/translations',json={**body,'confirmation_token':preview['confirmation_token']}).status_code==409
        execute(client,body,preview)
        for update in ({'target':'en'},{'glossary':[{'source':'self-attention','target':'自注意力'}]}):
            changed,fresh=prepare(client,data,**update)
            assert not fresh['blocks'][0]['cached']
            assert client.post('/api/translations',json={**changed,'confirmation_token':preview['confirmation_token']}).status_code==409
            execute(client,changed,fresh)
        assert len(calls)==3
        monkeypatch.setattr(agent,'PROTECTION_VERSION','controlled-new-policy')
        assert not client.post('/api/translations/preview',json=body).json()['blocks'][0]['cached']
        # Changing the prompt or credentials must not reuse another configuration's translation.
        prompt_root=tmp_path/'new-prompt';(prompt_root/'prompts').mkdir(parents=True)
        (prompt_root/'prompts/translation-v1.md').write_text('Changed controlled prompt',encoding='utf-8')
        monkeypatch.setattr(translation,'PROJECT_ROOT',prompt_root)
        assert not client.post('/api/translations/preview',json=body).json()['blocks'][0]['cached']
    with TestClient(create_app(replace(settings,model_api_key='different-key'))) as client:
        assert not client.post('/api/translations/preview',json=body).json()['blocks'][0]['cached']
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        _,preview=prepare(client,data)
        response=client.post('/api/translations',json={**body,'confirmation_token':preview['confirmation_token']})
        assert response.status_code==503 and '配置缺失' in response.text
    def network(*args):raise ModelError('受控网络连接失败',2)
    monkeypatch.setattr(agent,'complete',network)
    with TestClient(create_app(settings)) as client:
        _,preview=prepare(client,data,target='en')
        response=client.post('/api/translations',json={**body,'target':'en','confirmation_token':preview['confirmation_token']})
        assert response.status_code==503 and '已知请求2次' in response.text and '未知' in response.text


def test_corrupt_cache_and_io_are_explicit_not_silent_rebilling(tmp_path,monkeypatch):
    settings=Settings('https://controlled.invalid/v1','controlled','key',data_dir=tmp_path)
    calls=controlled(monkeypatch)
    with TestClient(create_app(settings)) as client:
        data,_=upload(client)
        body,preview=prepare(client,data)
        execute(client,body,preview)
        cache=next((tmp_path/'translations').glob('*.json'))
        cache.write_text('{broken',encoding='utf-8')
        assert client.post('/api/translations/preview',json=body).status_code==409 and len(calls)==1
        def fail(*args,**kwargs):raise OSError('controlled full disk')
        monkeypatch.setattr(Path,'replace',fail)
        body,preview=prepare(client,data,target='en')
        response=client.post('/api/translations',json={**body,'confirmation_token':preview['confirmation_token']})
        assert response.status_code==500 and '缓存无法读写' in response.text
        assert list((tmp_path/'translations').glob('*.tmp'))==[]


def test_internal_lookup_bug_is_not_reported_as_missing_evidence(tmp_path,monkeypatch):
    def bug(*args):raise IndexError('controlled internal bug')
    monkeypatch.setattr(translation,'preview',bug)
    with TestClient(create_app(Settings(data_dir=tmp_path)),raise_server_exceptions=False) as client:
        response=client.post('/api/translations/preview',json={'document_id':str(uuid4()),'evidence_id':str(uuid4())})
        assert response.status_code==500
