import { useCallback, useEffect, useState } from 'react'
import Reader from './Reader'
import { item, parseNotes, kinds, statuses, origins, type Item, type Note } from './Notes'
import { errorMessage, papers, record, request, result, type Evidence, type Paper } from './libraryApi'

const excerptFields:Record<string,string>={task:'任务',dataset:'数据集',split:'数据划分',metric:'指标定义与单位',protocol:'评估协议',
  code:'代码',data:'数据获取',dependencies:'依赖与环境',parameters:'参数',training:'训练配置',inference:'推理配置',hardware:'硬件'}
const stateLabels:Record<string,string>={clue:'原文线索，待核实',not_reported:'本次范围未识别，留空',not_provided:'原文明确未提供／不可用',ambiguous:'多套设置或表述冲突，留空',
  different:'设置文字不同',unknown:'可比性未知',text_match:'摘录文字一致，仍需人工核对'}
type Annotation={field:string;value:string;evidence_id:string;text:string}
type Selection={note_id:string;annotations:Annotation[]}
type Excerpt={value:string;evidence_id:string;text:string;origin:string}
type Clues={status:string;value:string|null;entries:Excerpt[];warning:string}
type Study={note_id:string;note_version:number;is_user_edited:boolean;source_mode:string;verification_run_id:string|null;
  paper_id:string;library_id:string;library_title:string;removed:boolean;title:string;authors:string[];document_id:string;file_version:number;filename:string;sha256:string;file_available:boolean;
  coverage:Record<string,unknown>|null;warnings:string[];cells:Record<string,{items:Item[];warnings:string[]}>;
  conditions:Record<string,Clues>;readiness:Record<string,Clues>;evidence:(Evidence&{text:string})[];
  scope:{physical_pages:number[];blocks:number;chars:number;truncated_evidence_ids:string[]}}
type Pair={left_note_id:string;right_note_id:string;status:string;warning:string;checks:{field:string;label:string;status:string}[];
  relations:{left_id:string;right_id:string;kind:string}[]}
type Report={id:string;created_at:string;content_sha256:string;warning:string;matrix_fields:Record<string,string>;condition_fields:Record<string,string>;
  readiness_fields:Record<string,string>;studies:Study[];pairs:Pair[]}
const strings=(value:unknown):value is string[]=>Array.isArray(value)&&value.every(v=>typeof v==='string')
const stringMap=(value:unknown):value is Record<string,string>=>record(value)&&Object.values(value).every(v=>typeof v==='string')
function clue(value:unknown):value is Clues {
  return record(value)&&typeof value.status==='string'&&['clue','not_reported','not_provided','ambiguous'].includes(value.status)
    &&(value.value===null||typeof value.value==='string')&&typeof value.warning==='string'&&Array.isArray(value.entries)
    &&value.entries.every(v=>record(v)&&['value','evidence_id','text','origin'].every(k=>typeof v[k]==='string'))
}
function study(value:unknown):value is Study {
  if(!record(value)||!['note_id','source_mode','paper_id','library_id','library_title','title','document_id','filename','sha256'].every(k=>typeof value[k]==='string')
    ||typeof value.note_version!=='number'||typeof value.file_version!=='number'||typeof value.is_user_edited!=='boolean'||typeof value.removed!=='boolean'
    ||typeof value.file_available!=='boolean'||!(value.verification_run_id===null||typeof value.verification_run_id==='string')
    ||!(value.coverage===null||record(value.coverage))||!strings(value.authors)||!strings(value.warnings)||!record(value.cells)
    ||!Object.values(value.cells).every(v=>record(v)&&Array.isArray(v.items)&&v.items.every(item)&&strings(v.warnings))
    ||!record(value.conditions)||!Object.values(value.conditions).every(clue)||!record(value.readiness)||!Object.values(value.readiness).every(clue)
    ||!record(value.scope)||!Array.isArray(value.scope.physical_pages)||!value.scope.physical_pages.every(v=>typeof v==='number')
    ||typeof value.scope.blocks!=='number'||typeof value.scope.chars!=='number'||!strings(value.scope.truncated_evidence_ids))return false
  return Array.isArray(value.evidence)&&value.evidence.every(v=>record(v)&&typeof v.id==='string'&&typeof v.text==='string'&&typeof v.physical_page==='number'
    &&Array.isArray(v.bbox)&&v.bbox.length===4&&v.bbox.every(x=>typeof x==='number')&&typeof v.page_width==='number'&&typeof v.page_height==='number')
}
function report(value:unknown):Report {
  if(!record(value)||!['id','created_at','content_sha256','warning'].every(k=>typeof value[k]==='string')
    ||!stringMap(value.matrix_fields)||!stringMap(value.condition_fields)||!stringMap(value.readiness_fields)
    ||!Array.isArray(value.studies)||value.studies.length<2||value.studies.length>5||!value.studies.every(study)
    ||!value.studies.every(s=>Object.keys(value.matrix_fields as object).every(k=>s.cells[k])&&Object.keys(value.condition_fields as object).every(k=>s.conditions[k])
      &&Object.keys(value.readiness_fields as object).every(k=>s.readiness[k]))
    ||!Array.isArray(value.pairs)||!value.pairs.every(v=>record(v)&&['left_note_id','right_note_id','status','warning'].every(k=>typeof v[k]==='string')
      &&Array.isArray(v.checks)&&v.checks.every(c=>record(c)&&['field','label','status'].every(k=>typeof c[k]==='string'))
      &&Array.isArray(v.relations)&&v.relations.every(r=>record(r)&&['left_id','right_id','kind'].every(k=>typeof r[k]==='string'))))throw Error('多文献比较快照格式无效')
  return value as Report
}

function StudyEditor({paper,onChange}:{paper:Paper;onChange:(paperId:string,value:Selection|null)=>void}) {
  const [documentId,setDocumentId]=useState(paper.documents[0]?.id??''),[notes,setNotes]=useState<Note[]>([]),[noteId,setNoteId]=useState('')
  const [blocks,setBlocks]=useState<Evidence[]>([]),[annotations,setAnnotations]=useState<Annotation[]>([])
  const [field,setField]=useState('hardware'),[evidenceId,setEvidenceId]=useState(''),[quote,setQuote]=useState(''),[value,setValue]=useState('')
  const [error,setError]=useState(''),[loading,setLoading]=useState(false)
  useEffect(()=>{
    let alive=true;setLoading(true);setError('');setNotes([]);setNoteId('');setBlocks([]);setAnnotations([]);setEvidenceId('');setQuote('');setValue('');onChange(paper.id,null)
    if(!documentId){setLoading(false);return()=>{alive=false}}
    void Promise.all([request(`/api/documents/${documentId}/notes`),request(`/api/documents/${documentId}`)]).then(([a,b])=>{
      const saved=parseNotes(a),parsed=result(b)
      if(alive){setNotes(saved);setNoteId(saved[0]?.id??'');setBlocks(parsed.evidence)}
    }).catch(e=>{if(alive)setError(errorMessage(e))}).finally(()=>{if(alive)setLoading(false)})
    return()=>{alive=false}
  },[documentId,paper.id,onChange])
  useEffect(()=>{onChange(paper.id,noteId?{note_id:noteId,annotations}:null)},[paper.id,noteId,annotations,onChange])
  function changeVersion(id:string,document=false){
    if(annotations.length&&!window.confirm('切换文件或笔记会清空本列未生成快照的人工摘录，是否继续？'))return
    setAnnotations([]);setQuote('');setValue('')
    if(document){setNoteId('');setDocumentId(id)}else setNoteId(id)
  }
  function add(){
    const block=blocks.find(b=>b.id===evidenceId)
    if(!block||!quote.trim()||!block.text.includes(quote)||!value.trim()||!quote.includes(value)){setError('摘录值须是逐字引文的一部分，引文须来自所选证据；不接受改写或补造。');return}
    setAnnotations(current=>[...current.filter(a=>a.field!==field),{field,evidence_id:evidenceId,text:quote,value}]);setError('');setQuote('');setValue('')
  }
  return <article className="study-editor"><h3>{paper.title||'题目未提供'}</h3>
    <label>比较文件<select aria-label={'比较文件 '+paper.id} value={documentId} onChange={e=>changeVersion(e.target.value,true)}>
      {paper.documents.map(d=><option key={d.id} value={d.id}>原文件v{d.version} · {d.original_name} · {d.paper_id.slice(0,8)}</option>)}</select></label>
    {loading?<p>正在载入笔记和原文…</p>:notes.length===0?<p>此文件没有已保存笔记，请先在文献库保存；不会自动调用模型补写。</p>:<label>比较笔记版本<select aria-label={'比较笔记 '+paper.id} value={noteId} onChange={e=>changeVersion(e.target.value)}>
      {notes.map(n=><option key={n.id} value={n.id}>v{n.version} · {n.is_user_edited?'人工修订':'保存快照'} · {origins[n.source_mode]??n.source_mode}</option>)}</select></label>}
    {noteId&&<details><summary>补充有出处的设置／复现线索（可选）</summary><p>自动只匹配明确“标签：值”。人工摘录补充有限范围，不覆盖已识别的其他设置，也不代表资源已可用。切换文件／笔记会清空本列摘录。</p>
      <label>摘录字段<select aria-label={'摘录字段 '+paper.id} value={field} onChange={e=>setField(e.target.value)}>{Object.entries(excerptFields).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      <label>原文证据<select aria-label={'摘录证据 '+paper.id} value={evidenceId} onChange={e=>{setEvidenceId(e.target.value);setQuote('');setValue('')}}><option value="">选择证据段落</option>
        {blocks.map(b=><option key={b.id} value={b.id}>物理页{b.physical_page} · {b.text.slice(0,100)}</option>)}</select></label>
      {evidenceId&&<blockquote>{blocks.find(b=>b.id===evidenceId)?.text}</blockquote>}
      <label>逐字引文<textarea aria-label={'摘录引文 '+paper.id} value={quote} maxLength={1200} onChange={e=>setQuote(e.target.value)}/></label>
      <label>摘录值（必须在引文中）<input aria-label={'摘录值 '+paper.id} value={value} maxLength={600} onChange={e=>setValue(e.target.value)}/></label>
      <button onClick={add}>保存本列摘录</button>{annotations.map(a=><p key={a.field}>{excerptFields[a.field]}：{a.value} <button onClick={()=>setAnnotations(list=>list.filter(v=>v.field!==a.field))}>移除该摘录</button></p>)}
    </details>}{error&&<p role="alert" className="error-message">{error}</p>}
  </article>
}

export default function Comparisons({active,targetId}:{active:boolean;targetId:string}) {
  const [options,setOptions]=useState<Paper[]>([]),[selected,setSelected]=useState<Paper[]>([]),[selections,setSelections]=useState<Record<string,Selection|null>>({})
  const [saved,setSaved]=useState<Report|null>(null),[reader,setReader]=useState<{study:Study;block:Evidence}|null>(null)
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[refresh,setRefresh]=useState(0)
  const changed=useCallback((id:string,value:Selection|null)=>setSelections(current=>({...current,[id]:value})),[])
  useEffect(()=>{
    if(!active)return
    let alive=true;setLoading(true);setError('')
    void papers('removed=false').then(all=>{if(alive)setOptions(all)}).catch(e=>{if(alive)setError(errorMessage(e))}).finally(()=>{if(alive)setLoading(false)})
    return()=>{alive=false}
  },[active,refresh])
  useEffect(()=>{
    if(!active||!targetId||saved?.id===targetId)return
    let alive=true;setBusy(true);setError('');setSaved(null);setReader(null)
    void request('/api/study-comparisons/'+targetId).then(value=>{const next=report(value);if(alive)setSaved(next)}).catch(e=>{if(alive)setError(errorMessage(e))}).finally(()=>{if(alive)setBusy(false)})
    return()=>{alive=false}
  },[active,targetId])
  function toggle(paper:Paper,checked:boolean){
    if(!checked&&selections[paper.id]?.annotations.length&&!window.confirm('移除此列会清空尚未生成快照的摘录，是否继续？'))return
    setSelected(list=>checked?[...list,paper]:list.filter(p=>p.id!==paper.id));setSelections(current=>{const next={...current};delete next[paper.id];return next})
  }
  async function generate(){
    setBusy(true);setError('')
    try{const next=report(await request('/api/study-comparisons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({studies:selected.map(p=>selections[p.id])})}))
      setSaved(next);setReader(null);window.location.hash='compare/'+next.id
    }catch(e){setError(errorMessage(e))}finally{setBusy(false)}
  }
  function source(study:Study,entry:{evidence_id:string;text:string;origin?:string},key:number|string){
    const block=study.evidence.find(b=>b.id===entry.evidence_id)
    return <details key={key}><summary>{entry.origin==='manual'?'人工摘录':entry.origin==='label'?'标签线索':'笔记引文'} · 物理页{block?.physical_page??'未知'}</summary><blockquote>{entry.text}</blockquote>
      {block?<button onClick={()=>setReader({study,block})}>查看此证据原文</button>:<p role="alert">快照证据缺失，无法定位。</p>}</details>
  }
  function clues(study:Study,value:Clues){return <><p>{stateLabels[value.status]??value.status}：{value.value??'—（留空）'}</p><p>{value.warning}</p>{value.entries.map((e,i)=>source(study,e,i))}</>}
  const canGenerate=selected.length>=2&&selected.length<=5&&selected.every(p=>selections[p.id]?.note_id)
  return <section className="library-panel library-body study-comparison" aria-label="多文献比较">
    <h2>多文献比较与复现准备清单</h2><p>选择2—5篇文献的明确文件和已保存笔记版本。矩阵复用有逐字出处的笔记主张，不自动综述、排名或运行训练；未保存的笔记编辑须先到文献库保存。</p>
    <button disabled={busy} onClick={()=>setRefresh(v=>v+1)}>刷新可选文献</button>
    {loading?<p>正在加载文献…</p>:options.length===0?<p>暂无可选文献，请先入库并保存笔记。</p>:options.map(p=><label key={p.id}><input aria-label={'选择比较 '+p.id} type="checkbox" checked={selected.some(v=>v.id===p.id)} disabled={busy||!selected.some(v=>v.id===p.id)&&selected.length>=5} onChange={e=>toggle(p,e.target.checked)}/>{p.title||'题目未提供'}</label>)}
    {selected.map(p=><StudyEditor key={p.id} paper={p} onChange={changed}/>)}
    <button disabled={busy||!canGenerate} onClick={()=>void generate()}>{busy?'正在处理比较快照…':'生成比较与清单（不调用模型）'}</button>
    <p>人工摘录仅在生成快照后持久保存；下方已保存结果不会随上方选择／摘录或之后编辑自动更新，需要重新生成。</p>
    {error&&<p role="alert" className="error-message">{error}</p>}
    {saved&&<><h3>已保存比较快照</h3><p>{saved.warning}</p><p>{saved.created_at} · <a href={'#compare/'+saved.id}>重新打开此快照</a></p><details><summary>快照ID与内容SHA256</summary>{saved.id}<br/>{saved.content_sha256}</details>
      {saved.studies.map((s,i)=><details key={s.note_id}><summary>文献{i+1} · {s.title||'题目未提供'} · 文件v{s.file_version}／笔记v{s.note_version}</summary>
        <p>{s.authors.join('、')||'作者未提供'} · {s.is_user_edited?'人工修订':'保存快照'} · 初始来源{origins[s.source_mode]??s.source_mode}{s.removed?' · 捕获时文献组已软移除':''}</p>
        <p>原文件{s.filename} · 原归属{s.paper_id} · 文件ID{s.document_id} · SHA256 {s.sha256}</p><p>笔记{s.note_id} · 核查任务{s.verification_run_id??'未提供'} · 捕获时PDF{s.file_available?'可用':'缺失'}</p>
        <p>输入物理页{s.scope.physical_pages.join('、')||'无'}，{s.scope.blocks}块／{s.scope.chars}字符；解析覆盖：{typeof s.coverage?.status==='string'?s.coverage.status:'未保存'}</p>
        {typeof s.coverage?.scope==='string'&&<p>解析范围：{s.coverage.scope}</p>}
        {typeof s.coverage?.parser_version==='string'&&<p>解析版本：{s.coverage.parser_version}</p>}
        {typeof s.coverage?.error==='string'&&<p role="alert" className="error-message">解析错误：{s.coverage.error}</p>}
        {s.warnings.map((w,j)=><p key={j} className="configuration-warning">{w}</p>)}{strings(s.coverage?.warnings)&&s.coverage.warnings.map((w,j)=><p key={j}>{w}</p>)}
      </details>)}
      <h3>任务与方法矩阵</h3><div className="comparison-scroll study-matrix"><table style={{minWidth:100+240*saved.studies.length}}><thead><tr><th>字段</th>{saved.studies.map((s,i)=><th key={s.note_id}>文献{i+1} · {s.title||'题目未提供'}</th>)}</tr></thead><tbody>
        {Object.entries(saved.matrix_fields).map(([field,label])=><tr key={field}><th>{label}</th>{saved.studies.map(s=><td key={s.note_id}>{s.cells[field].items.length===0&&<p>—（留空）</p>}
          {s.cells[field].items.map(v=><article key={v.id}><p>{v.content}</p><p>{kinds[v.content_type]??v.content_type} · 保存状态：{statuses[v.verification_status]??v.verification_status}</p><p>{v.verification_reason||'未保存核查理由'}</p>{v.quotes.map((q,i)=>source(s,q,i))}</article>)}{s.cells[field].warnings.map((w,i)=><p key={i}>{w}</p>)}</td>)}</tr>)}
      </tbody></table></div>
      <h3>可比条件与差异提示</h3><p>同名指标不等于同一单位或协议；“文字一致”也不证明条件完整或研究可比。</p>
      <div className="comparison-scroll study-matrix"><table style={{minWidth:100+240*saved.studies.length}}><thead><tr><th>条件</th>{saved.studies.map((s,i)=><th key={s.note_id}>文献{i+1}</th>)}</tr></thead><tbody>
        {Object.entries(saved.condition_fields).map(([key,label])=><tr key={key}><th>{label}</th>{saved.studies.map(s=><td key={s.note_id}>{clues(s,s.conditions[key])}</td>)}</tr>)}
      </tbody></table></div>
      {saved.pairs.map(p=><article key={p.left_note_id+p.right_note_id}><h4>文献{saved.studies.findIndex(s=>s.note_id===p.left_note_id)+1}／文献{saved.studies.findIndex(s=>s.note_id===p.right_note_id)+1} · {stateLabels[p.status]??p.status}</h4>
        <p>{p.warning}</p>{p.relations.some(r=>r.kind==='version')&&<p className="configuration-warning">用户已关联为同研究不同版本，不能当成独立研究或独立重复实验。</p>}
        <p>{p.checks.map(c=>`${c.label}：${stateLabels[c.status]??c.status}`).join('；')}</p></article>)}
      <h3>复现准备检查清单</h3><p>以下仅记录资源／配置线索；不检查链接可访问性、不安装依赖或执行代码，不计算就绪率／成功率。</p>
      <div className="comparison-scroll study-matrix"><table style={{minWidth:100+240*saved.studies.length}}><thead><tr><th>检查项</th>{saved.studies.map((s,i)=><th key={s.note_id}>文献{i+1}</th>)}</tr></thead><tbody>
        {Object.entries(saved.readiness_fields).map(([key,label])=><tr key={key}><th>{label}</th>{saved.studies.map(s=><td key={s.note_id}>{clues(s,s.readiness[key])}</td>)}</tr>)}
      </tbody></table></div>
    </>}
    {reader&&<><h3>比较出处原文 · {reader.study.title||'题目未提供'} · 文件v{reader.study.file_version}／笔记v{reader.study.note_version}</h3><p>{reader.block.text}</p><Reader key={reader.study.document_id} documentId={reader.study.document_id} selected={reader.block}/></>}
  </section>
}
