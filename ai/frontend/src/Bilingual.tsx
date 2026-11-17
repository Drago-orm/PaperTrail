import { useEffect, useRef, useState } from 'react'
import { errorMessage, record, request, result, type Evidence } from './libraryApi'

type Term={source:string;target:string}
type Preview={confirmation_token:string;configuration:{configured:boolean;missing:string[];errors:string[];service:string|null;model:string|null};
  target:string;scope:string;glossary:Term[];warning:string;blocks:{evidence_id:string;physical_page:number;section:string|null;text:string;cached:boolean;will_send:boolean;preservation:string|null;protected_count:number}[]}
type Translation={results:{evidence_id:string;document_id:string;physical_page:number;source:string;translation:string;status:string;target:string;model:string|null;service:string|null;
  cached:boolean;created_at:string;protected_count:number;terms:string[]}[];api_calls:number;usage:Record<string,unknown>|null;elapsed_ms:number;target:string;warning:string}
const strings=(value:unknown):value is string[]=>Array.isArray(value)&&value.every(v=>typeof v==='string')
const optionalString=(value:unknown):value is string|null=>value===null||typeof value==='string'
function preview(value:unknown):Preview {
  if(!record(value)||typeof value.confirmation_token!=='string'||!record(value.configuration)||typeof value.configuration.configured!=='boolean'
    ||!strings(value.configuration.missing)||!strings(value.configuration.errors)||!optionalString(value.configuration.service)||!optionalString(value.configuration.model)
    ||typeof value.target!=='string'||typeof value.scope!=='string'||typeof value.warning!=='string'||!Array.isArray(value.glossary)
    ||!value.glossary.every(t=>record(t)&&typeof t.source==='string'&&typeof t.target==='string')||!Array.isArray(value.blocks)||value.blocks.length===0
    ||!value.blocks.every(b=>record(b)&&typeof b.evidence_id==='string'&&typeof b.physical_page==='number'&&optionalString(b.section)&&typeof b.text==='string'
      &&typeof b.cached==='boolean'&&typeof b.will_send==='boolean'&&optionalString(b.preservation)&&typeof b.protected_count==='number'))throw Error('翻译发送预览格式无效')
  return value as Preview
}
function translation(value:unknown):Translation {
  if(!record(value)||!Array.isArray(value.results)||value.results.length===0||typeof value.api_calls!=='number'||typeof value.elapsed_ms!=='number'
    ||typeof value.target!=='string'||typeof value.warning!=='string'||!(value.usage===null||record(value.usage))
    ||!value.results.every(v=>record(v)&&['evidence_id','document_id','source','translation','target','created_at'].every(k=>typeof v[k]==='string')
      &&['translated','preserved','glossary'].includes(String(v.status))&&typeof v.physical_page==='number'&&typeof v.cached==='boolean'
      &&optionalString(v.model)&&optionalString(v.service)&&typeof v.protected_count==='number'&&strings(v.terms)))throw Error('译文对齐数据格式无效')
  return value as Translation
}
function glossary(raw:string):Term[]{
  const lines=raw.split('\n').map(v=>v.trim()).filter(Boolean)
  if(lines.length>20)throw Error('术语表最多20项')
  const terms=lines.map(line=>{const index=line.indexOf('=');if(index<1||!line.slice(index+1).trim())throw Error('术语表须每行原词=译词，不能留空')
    return{source:line.slice(0,index).trim(),target:line.slice(index+1).trim()}})
  if(new Set(terms.map(t=>t.source)).size!==terms.length)throw Error('术语原词不能重复')
  return terms
}
function stored(){
  try{
    const target=localStorage.getItem('papertrail.translation.target')??'zh-CN'
    return{target:target==='en'?'en':'zh-CN',raw:localStorage.getItem('papertrail.translation.glossary')??'',error:['en','zh-CN'].includes(target)?'':'保存的目标语言无效，请重新选择并预览'}
  }catch(e){return{target:'zh-CN',raw:'',error:'无法读取本机翻译设置：'+errorMessage(e)}}
}
const statusLabels:Record<string,string>={translated:'机器译文，待核对',preserved:'公式／代码或纯保护内容保留原文，未翻译',glossary:'仅术语替换，未调用模型'}

export default function Bilingual({documentId,selected,pageNumber,onSelect}:{documentId:string;selected:Evidence|null;pageNumber:number;onSelect:(value:Evidence)=>void}){
  const [initial]=useState(stored)
  const [target,setTarget]=useState(initial.target),[raw,setRaw]=useState(initial.raw),[scope,setScope]=useState('paragraph')
  const [blocks,setBlocks]=useState<Evidence[]>([]),[evidenceId,setEvidenceId]=useState(selected?.id??'')
  const [prepared,setPrepared]=useState<{key:string;data:Preview}|null>(null),[saved,setSaved]=useState<{key:string;data:Translation}|null>(null)
  const [error,setError]=useState(initial.error),[storageError,setStorageError]=useState(''),[operation,setOperation]=useState<'preview'|'translate'|null>(null),[loading,setLoading]=useState(true)
  const busy=operation!==null
  const key=JSON.stringify({document_id:documentId,evidence_id:evidenceId,scope,target,glossary:raw})
  const current=useRef(key);current.current=key
  const ready=prepared?.key===key?prepared.data:null,output=saved?.key===key?saved.data:null
  useEffect(()=>{
    let alive=true;setLoading(true)
    void request('/api/documents/'+documentId).then(value=>{const parsed=result(value);if(alive){setBlocks(parsed.evidence);setEvidenceId(id=>id||parsed.evidence.find(b=>b.physical_page===pageNumber)?.id||'')}})
      .catch(e=>{if(alive)setError(errorMessage(e))}).finally(()=>{if(alive)setLoading(false)})
    return()=>{alive=false}
  },[documentId])
  useEffect(()=>{if(selected)setEvidenceId(selected.id)},[selected?.id])
  async function prepare(){
    setOperation('preview');setError('');setPrepared(null);setSaved(null)
    const requestKey=key
    try{
      const terms=glossary(raw)
      try{localStorage.setItem('papertrail.translation.target',target);localStorage.setItem('papertrail.translation.glossary',raw);setStorageError('')}
      catch(e){setStorageError('本机术语设置未保存：'+errorMessage(e))}
      const data=preview(await request('/api/translations/preview',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({document_id:documentId,evidence_id:evidenceId,scope,target,glossary:terms})}))
      if(current.current===requestKey)setPrepared({key:requestKey,data})
    }catch(e){if(current.current===requestKey)setError(errorMessage(e))}finally{setOperation(null)}
  }
  async function translate(){
    if(!ready)return
    const requestKey=key
    setOperation('translate');setError('');setSaved(null)
    try{
      const data=translation(await request('/api/translations',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({document_id:documentId,evidence_id:evidenceId,scope,target,glossary:ready.glossary,confirmation_token:ready.confirmation_token})}))
      if(current.current===requestKey)setSaved({key:requestKey,data})
    }catch(e){if(current.current===requestKey)setError(errorMessage(e))}finally{setOperation(null)}
  }
  return <section className="bilingual" aria-label="段落双语阅读">
    <h3>段落／章节候选双语阅读</h3><p>先预览，再确认发送；翻译失败不影响原PDF阅读。公式／代码疑似无法可靠保护时整段保留，不假装完成翻译。</p>
    {loading?<p>正在读取可选文字段落…</p>:blocks.length===0?<p>无可用文字证据，扫描／空文本无法进行段落翻译。</p>:<label>所选段落<select aria-label="翻译证据" value={evidenceId} onChange={e=>{setEvidenceId(e.target.value);const block=blocks.find(b=>b.id===e.target.value);if(block)onSelect(block)}}>
      <option value="">选择段落</option>{blocks.map(b=><option key={b.id} value={b.id}>物理页{b.physical_page} · {b.text.slice(0,100)}</option>)}</select></label>}
    <label>翻译范围<select aria-label="翻译范围" value={scope} onChange={e=>setScope(e.target.value)}><option value="paragraph">所选单段</option><option value="section">所在章节候选（仅本页连续块）</option></select></label>
    <label>目标语言<select aria-label="翻译目标语言" value={target} onChange={e=>setTarget(e.target.value)}><option value="zh-CN">简体中文</option><option value="en">English</option></select></label>
    <label>专业术语表（最多20项，每行原词=译词）<textarea aria-label="翻译术语表" value={raw} maxLength={3400} onChange={e=>setRaw(e.target.value)}/></label>
    <p>术语原词精确匹配、英文词边界匹配；不能借术语映射改写数值、单位或代码。配置更改后需重新预览。</p>
    <button disabled={busy||!evidenceId} onClick={()=>void prepare()}>预览发送内容（不调用模型）</button>
    {storageError&&<p role="alert">{storageError}</p>}{error&&<p role="alert" className="error-message">{error}</p>}
    {ready&&<div className="translation-preview"><h4>本次内容发送说明</h4><p>服务：{ready.configuration.service??'未配置或配置无效'} · 模型：{ready.configuration.model??'未配置'} · 目标{ready.target}</p>
      <p>{ready.warning}</p><p>确认范围{ready.blocks.length}块；预计发送{ready.blocks.filter(b=>b.will_send).length}块。单次最多6块／4000字符、最多2次请求（含一次网络重试）。</p>
      <p>术语表：{ready.glossary.length?ready.glossary.map(t=>`${t.source} → ${t.target}`).join('；'):'空'}</p>
      {!ready.configuration.configured&&<p role="alert">配置缺失／错误：{[...ready.configuration.missing,...ready.configuration.errors].join('；')}</p>}
      {ready.blocks.map(b=><details key={b.evidence_id} open><summary>物理页{b.physical_page} · {b.cached?'命中缓存':b.will_send?'确认后发送':'本地保护，不发送'} · 章节{b.section??'未识别'}</summary>
        <p>{b.text}</p><small>证据ID {b.evidence_id}</small></details>)}
      <button disabled={busy||!ready.configuration.configured&&ready.blocks.some(b=>b.will_send)} onClick={()=>void translate()}>{busy?'正在处理，原文仍可阅读…':'确认上述范围并翻译／读取缓存'}</button>
    </div>}
    {busy&&!ready&&<p role="status">{operation==='preview'?'正在准备预览，未调用模型。':'已确认的前一范围仍在翻译／读取缓存，原文仍可阅读，请等待处理完成。'}</p>}
    {output&&<div className="translation-results" role="status"><p>本次已知请求{output.api_calls}次 · {output.elapsed_ms} ms · 已返回用量{output.usage?JSON.stringify(output.usage):output.api_calls===0?'未调用模型，不产生本次请求用量':'服务未报告'}</p>
      <p>{output.warning}</p>{output.results.map(v=><article key={v.evidence_id}><h4>物理页{v.physical_page} · {statusLabels[v.status]}{v.cached?' · 缓存复用':''}</h4>
        <p>目标{v.target} · {v.created_at} · 证据ID {v.evidence_id}</p><div className="bilingual-grid"><div><h4>保存原文</h4><p>{v.source}</p></div><div><h4>译文／保留内容</h4><p>{v.translation}</p></div></div>
        {v.terms.length>0&&<p>应用术语：{v.terms.join('、')}</p>}</article>)}</div>}
    <p><a href="https://cocotranslate.com/doc" target="_blank" rel="noreferrer noopener">打开椰子翻译（独立外部网页）</a>：此处不上传／同步文件；如在其页面手动上传，由该外部服务处理。未集成其API或内嵌服务。</p>
  </section>
}
