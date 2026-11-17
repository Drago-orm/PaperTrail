import { useEffect, useState } from 'react'
import { errorMessage, papers, record, request, type Paper } from './libraryApi'

const fields = { title:'题目', abstract:'摘要', authors:'作者', keywords:'关键词', tags:'标签', tasks:'任务',
  methods:'方法', datasets:'数据集', reading_status:'阅读状态', doi:'DOI', year:'年份' }
const relations: Record<string,string> = {version:'同研究不同版本',related:'相关但不同文献',distinct:'非重复'}
type Candidate = {id:string; title:string; relation:string|null; kind:string; reasons:string[]; differences:string[]}
type Entry = Paper & {tasks:string[]; methods:string[]; datasets:string[]; members:{id:string; title:string}[]; counts:{notes:number; idea_sources:number}}
type Comparison = {left:Entry; right:Entry; relation:string|null; links:{left_id:string;right_id:string;kind:string}[];
  token:string; kind:string; reasons:string[]; differences:string[]}
type Event = {id:string;status:string;primary_title:string;secondary_title:string;created_at:string;undo_conflict:string|null}
const strings = (value:unknown):value is string[]=>Array.isArray(value)&&value.every(v=>typeof v==='string')
function candidate(value:unknown):value is Candidate {
  return record(value)&&typeof value.id==='string'&&typeof value.title==='string'&&typeof value.kind==='string'
    &&(value.relation===null||typeof value.relation==='string')&&strings(value.reasons)&&strings(value.differences)
}
function entry(value:unknown):value is Entry {
  if(!record(value)||typeof value.id!=='string'||typeof value.removed!=='boolean'||!record(value.counts)
    ||typeof value.counts.notes!=='number'||typeof value.counts.idea_sources!=='number'
    ||!Array.isArray(value.members)||!value.members.every(v=>record(v)&&typeof v.id==='string'&&typeof v.title==='string')
    ||!Array.isArray(value.documents)||!value.documents.every(v=>record(v)&&['id','paper_id','original_name','sha256','parse_status'].every(k=>typeof v[k]==='string')&&typeof v.version==='number'))return false
  return Object.keys(fields).every(key=>['authors','keywords','tags','tasks','methods','datasets'].includes(key)?strings(value[key])
    :key==='year'?value.year===null||typeof value.year==='number':typeof value[key]==='string')
}
function comparison(value:unknown):Comparison {
  if(!record(value)||!entry(value.left)||!entry(value.right)||typeof value.token!=='string'||typeof value.kind!=='string'
    ||!(value.relation===null||typeof value.relation==='string')||!strings(value.reasons)||!strings(value.differences)
    ||!Array.isArray(value.links)||!value.links.every(v=>record(v)&&['left_id','right_id','kind'].every(k=>typeof v[k]==='string')))throw Error('差异比较数据格式无效')
  return value as Comparison
}
function history(value:unknown):Event[] {
  if(!Array.isArray(value)||!value.every(v=>record(v)&&['id','status','primary_title','secondary_title','created_at'].every(k=>typeof v[k]==='string')
    &&(v.undo_conflict===null||typeof v.undo_conflict==='string')))throw Error('合并历史数据格式无效')
  return value as Event[]
}
function display(value:unknown):string {
  if(value===null||value===undefined||value===''||Array.isArray(value)&&!value.length)return '未提供'
  return Array.isArray(value)?value.join('；'):String(value)
}

export default function Duplicates({paperId,refresh,disabled,onChanged}:{paperId:string;refresh:number;disabled:boolean;onChanged:()=>Promise<void>}) {
  const [options,setOptions]=useState<Paper[]>([]),[list,setList]=useState<Candidate[]>([]),[events,setEvents]=useState<Event[]>([])
  const [rightId,setRightId]=useState(''),[view,setView]=useState<Comparison|null>(null)
  const [choices,setChoices]=useState<Record<string,'left'|'right'>>({})
  const [includeDistinct,setIncludeDistinct]=useState(false),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true)
  const [error,setError]=useState(''),[notice,setNotice]=useState('')
  useEffect(()=>{
    let alive=true;setLoading(true);setError('');setView(null)
    void Promise.all([papers('removed=false'),request(`/api/papers/${paperId}/duplicates?include_distinct=${includeDistinct}`),request(`/api/papers/${paperId}/merges`)]).then(([all,c,h])=>{
      if(!Array.isArray(c)||!c.every(candidate))throw Error('重复候选数据格式无效')
      const saved=history(h)
      if(alive){setOptions(all.filter(p=>p.id!==paperId));setList(c);setEvents(saved);setRightId(id=>all.some(p=>p.id===id&&p.id!==paperId)?id:c[0]?.id??'')}
    }).catch(e=>{if(alive)setError(errorMessage(e))}).finally(()=>{if(alive)setLoading(false)})
    return()=>{alive=false}
  },[paperId,refresh,includeDistinct])
  async function compare(id=rightId){
    setBusy(true);setError('');setNotice('');setView(null);setRightId(id)
    try{const saved=comparison(await request('/api/paper-comparison?'+new URLSearchParams({left_id:paperId,right_id:id})))
      setView(saved);setChoices(Object.fromEntries(Object.keys(fields).map(key=>[key,'left'])))
    }catch(e){setError(errorMessage(e))}finally{setBusy(false)}
  }
  async function mutate(kind:'version'|'related'|'distinct'|'merge'|null){
    if(!view)return
    if(kind==='merge'&&!window.confirm('确认将右侧元数据条目汇总到左侧，并采用表格中选择的字段？原文件、笔记和Idea归属保留，可在合并历史中撤销。'))return
    setBusy(true);setError('');setNotice('')
    try{
      await request(kind==='merge'?'/api/merges':'/api/paper-relations',{method:kind==='merge'?'POST':'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({left_id:view.left.id,right_id:view.right.id,token:view.token,...(kind==='merge'?{fields:choices}:{kind})})})
      setView(null);await onChanged();setNotice(kind==='merge'?'已合并元数据条目；原文件版本与来源ID保留。':kind===null?'已清除这两个主条目的关系标记。':'已保存关系：'+relations[kind])
    }catch(e){setError(errorMessage(e))}finally{setBusy(false)}
  }
  async function undo(id:string){
    setBusy(true);setError('');setNotice('')
    try{await request(`/api/merges/${id}/undo`,{method:'POST'});setView(null);await onChanged();setNotice('已撤销合并；保留后续附件、笔记、Idea及未冲突字段的修改。')}
    catch(e){setError(errorMessage(e))}finally{setBusy(false)}
  }
  return <section className="duplicate-review" aria-label="去重与版本关联">
    <h3>去重与版本关联</h3>
    <p>规则仅生成候选，不调用模型、不自动合并。预印本和正式版优先关联；摘要原文并列供人工比较，未填写信息不推断。</p>
    {disabled&&<p>请先保存上方元数据编辑，再比较或确认操作。</p>}
    <label><input type="checkbox" checked={includeDistinct} onChange={e=>setIncludeDistinct(e.target.checked)}/>显示已标记非重复</label>
    {loading?<p>正在查找候选和合并历史…</p>:<>
      {list.length===0?<p>没有规则候选或已关联条目；可手动选择其他文献比较，不能据此断言没有重复。</p>:<ul>{list.map(value=><li key={value.id}>
        <button disabled={busy||disabled} onClick={()=>void compare(value.id)}>{value.title||'题目未提供'}</button>
        <p>{value.relation?relations[value.relation]:value.kind==='exact_file'?'附件完全重复':'疑似重复，待人工判定'} · {[...value.reasons,...value.differences].join('；')}</p>
      </li>)}</ul>}
      <label>手动比较文献<select aria-label="手动比较文献" disabled={busy||disabled} value={rightId} onChange={e=>{setRightId(e.target.value);setView(null)}}>
        <option value="">选择右侧条目</option>{options.map(p=><option key={p.id} value={p.id}>{p.title||'题目未提供'}</option>)}</select></label>
      <button disabled={!rightId||busy||disabled} onClick={()=>void compare()}>比较差异</button>
    </>}
    {view&&<>
      <p>{view.kind==='exact_file'?'发现相同文件哈希':'尚不能认定为重复'}；{[...view.reasons,...view.differences].join('；')}</p>
      {view.links.map(link=><p key={link.left_id+link.right_id}>已保存关系：{relations[link.kind]??link.kind} · 原条目 {link.left_id}／{link.right_id}</p>)}
      <div className="comparison-scroll"><table><thead><tr><th>字段</th><th>左侧主条目</th><th>右侧条目</th><th>合并采用</th></tr></thead><tbody>
        {Object.entries(fields).map(([key,label])=><tr key={key}><th>{label}</th><td>{display(view.left[key as keyof Entry])}</td><td>{display(view.right[key as keyof Entry])}</td><td>
          <select aria-label={'合并字段 '+key} value={choices[key]??'left'} disabled={busy||disabled} onChange={e=>setChoices(v=>({...v,[key]:e.target.value==='right'?'right':'left'}))}>
            <option value="left">左侧</option><option value="right">右侧</option></select></td></tr>)}
      </tbody></table></div>
      {[view.left,view.right].map((side,i)=><div key={side.id}><h4>{i===0?'左侧主条目':'右侧条目'} · {side.title||'题目未提供'}</h4>
        <p>原条目 {side.members.length} 个 · 笔记快照 {side.counts.notes} · Idea证据关联 {side.counts.idea_sources}</p>
        {side.members.map(member=><p key={member.id}>原条目：{member.title||'题目未提供'} · {member.id}</p>)}
        {side.documents.length===0&&<p>无附件。</p>}{side.documents.map(doc=><p key={doc.id}>文件 v{doc.version} · {doc.original_name} · {doc.parse_status}<br/>
          原归属 {doc.paper_id} · 文件ID {doc.id}<br/>SHA256 {doc.sha256} · <a href={`/api/documents/${doc.id}/file`} target="_blank" rel="noreferrer">打开原文件</a></p>)}
      </div>)}
      <p>版本关联、相关或非重复只保存关系，两个条目与附件均保留。确认合并仅更新所选元数据、汇总展示附件；原始ID与文件版本不会重新编号。</p>
      <button disabled={busy||disabled} onClick={()=>void mutate('version')}>关联为同研究不同版本</button>
      <button disabled={busy||disabled} onClick={()=>void mutate('related')}>标记相关但不同文献</button>
      <button disabled={busy||disabled} onClick={()=>void mutate('distinct')}>标记非重复</button>
      {view.relation&&<button disabled={busy||disabled} onClick={()=>void mutate(null)}>清除关系标记</button>}
      <button disabled={busy||disabled||view.right.members.length>1||view.relation==='distinct'} onClick={()=>void mutate('merge')}>确认合并到左侧主条目</button>
      {view.right.members.length>1&&<p>右侧已汇总其他条目，请先撤销右侧合并，或以该条目作为左侧主条目逐个汇总。</p>}
    </>}
    <h4>合并历史</h4>
    {!loading&&events.length===0&&<p>此条目没有合并历史。</p>}{events.map(event=><article key={event.id}>
      <p>{event.secondary_title||'题目未提供'} → {event.primary_title||'题目未提供'} · {event.status==='applied'?'已合并':'已撤销'} · {event.created_at}</p>
      {event.status==='applied'&&<><button disabled={busy||disabled||!!event.undo_conflict} onClick={()=>void undo(event.id)}>撤销此合并</button>
        {event.undo_conflict&&<p role="alert">{event.undo_conflict}</p>}</>}
    </article>)}
    {error&&<p role="alert" className="error-message">{error} 请重新比较或刷新后再试。</p>}{notice&&<p role="status">{notice}</p>}
  </section>
}
