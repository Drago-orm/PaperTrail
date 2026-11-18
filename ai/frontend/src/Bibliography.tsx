import { useState } from 'react'
import { errorMessage, identifiers, record, request, type Paper } from './libraryApi'

type Entry = {id?:string;metadata:{title:string;authors:string[];abstract:string;doi:string;year:number|null;identifiers:Record<string,string[]>};
  unmapped:Record<string,unknown>;candidates:{id:string;title:string;removed:boolean;reasons:string[]}[]}
type Preview = {confirmation_token:string;records:Entry[];warnings:string[];imported:boolean;sha256:string}
const strings=(v:unknown):v is string[]=>Array.isArray(v)&&v.every(x=>typeof x==='string')
function preview(value:unknown):Preview {
  if(!record(value)||typeof value.confirmation_token!=='string'||typeof value.sha256!=='string'||typeof value.imported!=='boolean'
    ||!strings(value.warnings)||!Array.isArray(value.records)||!value.records.every(e=>record(e)&&(e.id===undefined||typeof e.id==='string')
      &&record(e.metadata)&&typeof e.metadata.title==='string'&&typeof e.metadata.abstract==='string'&&typeof e.metadata.doi==='string'&&strings(e.metadata.authors)
      &&(e.metadata.year===null||typeof e.metadata.year==='number')&&identifiers(e.metadata.identifiers)&&record(e.unmapped)
      &&Array.isArray(e.candidates)&&e.candidates.every(c=>record(c)&&typeof c.id==='string'&&typeof c.title==='string'
        &&typeof c.removed==='boolean'&&strings(c.reasons))))throw Error('元数据预览格式无效')
  return value as Preview
}

export default function Bibliography({items,selected,disabled,onImported,onChoose}:{items:Paper[];selected:Paper|null;disabled:boolean;
  onImported:()=>void;onChoose:(id:string)=>Promise<void>}) {
  const [format,setFormat]=useState<'bib'|'ris'>('bib'),[file,setFile]=useState<File|null>(null)
  const [view,setView]=useState<Preview|null>(null),[busy,setBusy]=useState(false)
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[warnings,setWarnings]=useState<string[]>([])
  async function inspect(confirm=false){
    if(!file||confirm&&!view)return
    setBusy(true);setError('');setNotice('');setWarnings([])
    if(!confirm)setView(null)
    try{
      if(!file.size||file.size>2*1024*1024)throw Error('请选择非空文件，最大2 MiB')
      const body=new FormData();body.append('file',file);body.append('format',format)
      if(confirm&&view)body.append('confirmation_token',view.confirmation_token)
      const result=preview(await request('/api/bibliography/'+(confirm?'import':'preview'),{method:'POST',body}))
      setView(result)
      if(result.imported){onImported();setNotice(`已新建${result.records.length}个元数据条目。重复条目保留，请在文献信息中人工比较；未创建附件。`)}
    }catch(e){setError(errorMessage(e));if(confirm)setView(null)}finally{setBusy(false)}
  }
  async function download(selection:Paper[]){
    setBusy(true);setError('');setNotice('');setWarnings([])
    try{
      const data=await request('/api/bibliography/export',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({format,paper_ids:selection.map(p=>p.id)})})
      if(!record(data)||typeof data.text!=='string'||typeof data.filename!=='string'||typeof data.count!=='number'||!strings(data.warnings))throw Error('元数据导出格式无效')
      setWarnings(data.warnings)
      const url=URL.createObjectURL(new Blob([data.text],{type:'text/plain;charset=utf-8'}))
      const link=document.createElement('a');link.href=url;link.download=data.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
      setNotice(`已生成${data.count}条UTF-8元数据并触发浏览器下载；请在下载目录核对文件。`)
    }catch(e){setError(errorMessage(e))}finally{setBusy(false)}
  }
  const locked=busy||disabled
  return <details className="bibliography"><summary>文献元数据交换（BibTeX／RIS）</summary>
    <p>本地解析，不调用模型或联网取文；不操作Zotero、不创建附件。预览会列出未映射字段，确认后这些字段不入库；请保留原文件。</p>
    <label>交换格式<select aria-label="元数据格式" disabled={locked} value={format} onChange={e=>{setFormat(e.target.value==='ris'?'ris':'bib');setView(null);setError('');setNotice('');setWarnings([])}}>
      <option value="bib">BibTeX (.bib)</option><option value="ris">RIS (.ris)</option></select></label>
    <input aria-label="元数据文件" type="file" disabled={locked} accept=".bib,.ris,text/plain" onChange={e=>{setFile(e.target.files?.[0]??null);setView(null);setError('');setNotice('');setWarnings([])}}/>
    <p>UTF-8／UTF-8 BOM，单次1—100条，最大2 MiB。按所选格式校验实际内容，不依赖扩展名。</p>
    <button disabled={locked||!file} onClick={()=>void inspect()}>预览元数据（不写入）</button>
    <button disabled={locked||!view||view.imported} onClick={()=>void inspect(true)}>确认新建全部条目（含重复候选）</button>
    <p>导出当前已保存值；不包含未保存编辑或笔记。通用条目类型／新引用键，其他标识符分类限制见结果提示。</p>
    <button disabled={locked||!selected} onClick={()=>selected&&void download([selected])}>导出所选文献</button>
    <button disabled={locked||items.length<1||items.length>100} onClick={()=>void download(items)}>导出当前列表（{items.length}条）</button>
    {items.length>100&&<p>当前列表超过100条，请先筛选，不会静默截断。</p>}
    {busy&&<p role="status">正在本地处理元数据…</p>}
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {[...warnings,...(view?.warnings??[])].map((w,i)=><p className="configuration-warning" key={i}>{w}</p>)}
    {view&&<div className="bibliography-results"><p>{view.imported?'已导入':'待确认'} · 文件SHA256：{view.sha256}</p>
      {view.records.map((entry,i)=><article key={entry.id??i}><h4>{i+1}. {entry.metadata.title||'题目未提供'}</h4>
        <p>作者：{entry.metadata.authors.join('；')||'未提供'} · 年份：{entry.metadata.year??'未提供'} · DOI：{entry.metadata.doi||'未提供'}</p>
        <p>其他标识符：{Object.entries(entry.metadata.identifiers).map(([key,values])=>`${key}: ${values.join('；')}`).join(' / ')||'未提供'}</p>
        <p className="pre-wrap">摘要：{entry.metadata.abstract||'未提供'}</p>
        {Object.keys(entry.unmapped).length>0&&<details><summary>不入库的字段（{Object.keys(entry.unmapped).length}项）</summary><pre>{JSON.stringify(entry.unmapped,null,2)}</pre></details>}
        {entry.candidates.length?<ul>{entry.candidates.map(c=><li key={c.id}>{c.title||'题目未提供'} · {c.id}{c.removed?'（移除区）':''}：{c.reasons.join('；')}</li>)}</ul>:<p>没有规则候选；不代表没有重复。</p>}
        {view.imported&&entry.id&&<button disabled={locked} onClick={()=>{setError('');void onChoose(entry.id!).catch(e=>setError(errorMessage(e)))}}>打开此条目并人工去重</button>}
      </article>)}</div>}
  </details>
}
