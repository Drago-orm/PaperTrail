import { useEffect, useRef, useState } from 'react'
import Reader from './Reader'
import ExportActions from './ExportActions'
import { errorMessage, record, request, result, type Evidence } from './libraryApi'
import { idea, ideas, sources, sourceKey, type Idea, type Source } from './ideasApi'

const states: Record<string,string> = { draft:'草稿',confirmed:'用户已确认',testing:'验证中',archived:'已归档' }
const kinds: Record<string,string> = { author_statement:'作者陈述',system_analysis:'系统判断',research_idea:'研究设想' }
const origins: Record<string,string> = { live:'真实 API',replay:'固定回放',unknown:'未知' }
const versionLabel = (value:Idea) => value.is_user_edited?'人工编辑':value.generation_run_id?'模型草稿':'来源未记录'
const labels = { question:'研究问题',hypothesis:'待验证假设',derivation:'来源推导',difference:'与原作的拟议差异',resources:'所需资源与条件',failure_conditions:'失败条件',novelty_checks:'待核实的新颖性问题（每行一项）',
  model:'共同模型',dataset:'共同数据集',split:'共同划分',factor:'唯一变化因素',control:'对照值',treatment:'干预值',constants:'其余固定设置（每行名称=值）',metric:'评估指标',evaluation:'统一评估协议',pending:'待确认条件（每行一项）' }
type Form = Record<keyof typeof labels,string>
const blank = (): Form => Object.fromEntries(Object.keys(labels).map(k=>[k,''])) as Form
type Task = { id:string; status:string; error:string|null; stage:string; ideaId:string|null; elapsed:number|null; calls:number|null; usage:Record<string,unknown>|null; cached:boolean }
function task(value: unknown): Task {
  if (!record(value) || typeof value.id!=='string' || typeof value.status!=='string' || !record(value.input_scope)
    || !(value.error===null || typeof value.error==='string') || !(value.elapsed_ms===null || typeof value.elapsed_ms==='number')) throw new Error('Idea任务格式无效')
  const out = record(value.output) ? value.output : {}
  return {id:value.id,status:value.status,error:value.error,stage:typeof value.input_scope.stage==='string'?value.input_scope.stage:'阶段缺失',
    ideaId:typeof out.idea_id==='string'?out.idea_id:null,elapsed:value.elapsed_ms,calls:typeof out.api_calls==='number'?out.api_calls:null,
    usage:record(out.usage)?out.usage:null,cached:out.cached===true}
}
const lines = (value:string) => value.split('\n').map(v=>v.trim()).filter(Boolean)

export default function Ideas({ targetId, active }: { targetId: string; active: boolean }) {
  const [list,setList] = useState<Idea[]>([]), [options,setOptions] = useState<Source[]>([])
  const [selected,setSelected] = useState<Idea|null>(null), [versions,setVersions] = useState<Idea[]>([])
  const [form,setForm] = useState<Form>(blank), [checked,setChecked] = useState<string[]>([])
  const [pinned,setPinned] = useState<Source[]>([])
  const [exportIds,setExportIds] = useState<string[]>([])
  const [status,setStatus] = useState('draft'), [q,setQ] = useState(''), [filter,setFilter] = useState('')
  const [interest,setInterest] = useState(''), [resources,setResources] = useState('')
  const [error,setError] = useState(''), [notice,setNotice] = useState(''), [busy,setBusy] = useState(false)
  const [loading,setLoading] = useState(true), [refresh,setRefresh] = useState(0), [dirty,setDirty] = useState(false)
  const dirtyRef = useRef(false)
  const openSequence = useRef(0)
  const [job,setJob] = useState<Task|null>(null)
  const [reader,setReader] = useState<{source:Source; evidence:Evidence|null}|null>(null)
  const root = versions.find(value=>value.version===1)
  const pool = [...options,...(selected?.sources??[]),...pinned].filter((s,i,a)=>s.note_id && s.item_id && a.findIndex(v=>sourceKey(v)===sourceKey(s))===i)
  useEffect(()=>{dirtyRef.current=dirty},[dirty])
  useEffect(()=>{
    if(!active)return
    let alive=true;setLoading(true);setError('')
    void Promise.all([request('/api/ideas?'+new URLSearchParams({q,status:filter})),request('/api/idea-sources')]).then(([a,b])=>{
      const saved=ideas(a), available=sources(b);if(alive){setList(saved);setOptions(available)}
    }).catch(e=>{if(alive)setError(errorMessage(e))}).finally(()=>{if(alive)setLoading(false)})
    return()=>{alive=false}
  },[active,q,filter,refresh])
  function apply(value:Idea, history:Idea[]){
    const e=value.experiment
    setSelected(value);setVersions(history);setStatus(value.status)
    setForm({...blank(),question:value.question,hypothesis:value.hypothesis,derivation:value.derivation,difference:value.difference,
      resources:value.resources,failure_conditions:value.failure_conditions,novelty_checks:value.novelty_checks.join('\n'),
      model:e?.control.model??'',dataset:e?.control.dataset??'',split:e?.control.split??'',factor:e?.changed_factor??'',
      control:e?.control.settings[e.changed_factor]??'',treatment:e?.treatment.settings[e.changed_factor]??'',
      constants:e?Object.entries(e.control.settings).filter(([k])=>k!==e.changed_factor).map(([k,v])=>`${k}=${v}`).join('\n'):'',
      metric:e?.metric??'',evaluation:e?.evaluation??'',pending:e?.pending_confirmations.join('\n')??''})
    setChecked([...new Set(value.sources.filter(s=>s.note_id && s.item_id).map(sourceKey))]);setPinned(value.sources);setDirty(false);setReader(null)
  }
  async function open(id:string, confirm=true){
    if(confirm && dirtyRef.current && !window.confirm('切换Idea会放弃尚未保存的编辑，是否继续？'))return
    const sequence=++openSequence.current
    setError('');setBusy(true)
    try{const [a,b]=await Promise.all([request('/api/ideas/'+id),request('/api/ideas/'+id+'/versions')]);if(sequence===openSequence.current)apply(idea(a),ideas(b))}
    catch(e){if(sequence===openSequence.current)setError(errorMessage(e))}finally{if(sequence===openSequence.current)setBusy(false)}
  }
  useEffect(()=>{if(active && targetId)void open(targetId)},[active,targetId])
  useEffect(()=>{
    if(!active || !selected)return
    let alive=true
    void request('/api/ideas/'+selected.id).then(raw=>{const fresh=idea(raw);if(alive)setSelected(current=>current?.id===fresh.id?{...current,sources:fresh.sources}:current)})
      .catch(e=>{if(alive)setError(errorMessage(e))})
    return()=>{alive=false}
  },[active,refresh,selected?.id])
  useEffect(()=>{
    if(!job || !['pending','running'].includes(job.status))return
    let alive=true
    const timer=window.setTimeout(()=>{void request('/api/reading-runs/'+job.id).then(raw=>{
      const next=task(raw);if(!alive)return;setJob(next)
      if(next.status==='succeeded'){
        setRefresh(v=>v+1);setNotice(next.cached?'已复用已有草稿，未调用模型；人工版本保留。':'新草稿已生成，原有Idea和人工修订保留。')
        if(next.ideaId && !dirtyRef.current)void open(next.ideaId,false)
      }
    }).catch(e=>{if(alive)setError(errorMessage(e))})},800)
    return()=>{alive=false;window.clearTimeout(timer)}
  },[job,refresh])
  function chosen(){return pool.filter(value=>checked.includes(sourceKey(value))).map(value=>({note_id:value.note_id,item_id:value.item_id}))}
  async function generate(){
    setBusy(true);setError('');setNotice('')
    try{setJob(task(await request('/api/idea-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sources:chosen(),interest,resources})})))}
    catch(e){setError(errorMessage(e))}finally{setBusy(false)}
  }
  async function save(){
    setBusy(true);setError('')
    try{
      const constants:Record<string,string>={}
      for(const line of lines(form.constants)){
        const index=line.indexOf('=');if(index<1 || !line.slice(index+1).trim())throw new Error('固定设置须使用名称=值')
        const key=line.slice(0,index).trim();if(key===form.factor || key in constants)throw new Error('固定设置不能重名或覆盖变化因素')
        constants[key]=line.slice(index+1).trim()
      }
      const common={model:form.model,dataset:form.dataset,split:form.split}
      const draft={question:form.question,hypothesis:form.hypothesis,derivation:form.derivation,difference:form.difference,
        resources:form.resources,failure_conditions:form.failure_conditions,novelty_checks:lines(form.novelty_checks),sources:chosen(),
        experiment:{control:{...common,settings:{...constants,[form.factor]:form.control}},treatment:{...common,settings:{...constants,[form.factor]:form.treatment}},
          changed_factor:form.factor,metric:form.metric,evaluation:form.evaluation,pending_confirmations:lines(form.pending)}}
      const saved=idea(await request('/api/ideas',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({draft,status,base_idea_id:selected?.id??null})}))
      const history=ideas(await request('/api/ideas/'+saved.id+'/versions'));apply(saved,history);setRefresh(v=>v+1);setNotice('已保存为新的Idea版本。')
    }catch(e){setError(errorMessage(e))}finally{setBusy(false)}
  }
  async function locate(source:Source){
    setError('');setReader(null)
    try{
      const data=result(await request('/api/documents/'+source.document_id))
      const block=source.evidence_id?data.evidence.find(value=>value.id===source.evidence_id):null
      if(source.evidence_id && !block)throw new Error('来源证据已缺失，无法提供高亮')
      setReader({source,evidence:block?{...block}:null})
    }catch(e){setError(errorMessage(e))}
  }
  return <section className="library-panel library-body idea-library" aria-label="Idea库">
    <h2>待验证 Idea 库</h2>
    <p>来源内容与下方设想分开。自动核查不保证准确；尚未执行外部查新，不承诺首次创新、效果提升或发表价值。</p>
    <div className="filters"><label>筛选研究问题<input aria-label="Idea筛选" value={q} onChange={e=>setQ(e.target.value)}/></label>
      <label>状态筛选<select aria-label="Idea状态筛选" value={filter} onChange={e=>setFilter(e.target.value)}><option value="">全部</option>{Object.entries(states).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label></div>
    <button onClick={()=>setRefresh(v=>v+1)}>刷新 Idea 与来源</button>
    <button disabled={busy} onClick={()=>{if(!dirty || window.confirm('放弃当前未保存编辑？')){setSelected(null);setVersions([]);setForm(blank());setDirty(false);setStatus('draft');setNotice('正在新增人工Idea，请填写验证计划和来源。')}}}>新增人工 Idea</button>
    <p>勾选需导出的已保存 Idea 版本（最多10个）；筛选或新修订后，已勾选的版本仍保留。导出不生成新内容。</p>
    {loading?<p>正在读取 Idea…</p>:list.length===0?<p>暂无符合条件的 Idea。</p>:<ul className="paper-list">{list.map(value=><li key={value.id}>
      <label><input type="checkbox" aria-label={'导出 Idea '+value.id} checked={exportIds.includes(value.id)}
        disabled={!exportIds.includes(value.id) && exportIds.length>=10} onChange={e=>setExportIds(ids=>e.target.checked?[...ids,value.id]:ids.filter(id=>id!==value.id))}/>导出 v{value.version}</label>
      <button disabled={busy} onClick={()=>void open(value.id)}>{value.question}</button><span>v{value.version} · {states[value.status]} · {versionLabel(value)}</span></li>)}</ul>}
    <ExportActions key={exportIds.join(',')} ideaIds={exportIds} disabled={dirty} />
    {exportIds.length>0 && <button onClick={()=>setExportIds([])}>清空导出选择（{exportIds.length}）</button>}
    <h3>已支持的来源条目（最多6条／3个文件）</h3>
    {!pool.length && <p>暂无可用来源，请先完成原文核查；部分支持条目不作为生成依据。</p>}
    {pool.map(value=><label className="idea-source" key={sourceKey(value)}><input type="checkbox" aria-label={'来源 '+sourceKey(value)} checked={checked.includes(sourceKey(value))}
      disabled={busy || !checked.includes(sourceKey(value)) && checked.length>=6} onChange={e=>{setChecked(v=>e.target.checked?[...v,sourceKey(value)]:v.filter(k=>k!==sourceKey(value)));
        setPinned(v=>e.target.checked?[...v.filter(s=>sourceKey(s)!==sourceKey(value)),value]:v.filter(s=>sourceKey(s)!==sourceKey(value)));
        setDirty(!!selected || Object.values(form).some(v=>v!==''))}}/>
      {value.paper_title||'题目未提供'} · 文件v{value.file_version}／笔记v{value.note_version} · 初始精读{origins[value.source_mode??'']??'未记录'} · {kinds[value.content_type??'']??'类型未记录'}：{value.content}</label>)}
    <div className="filters"><label>任务兴趣<input aria-label="Idea兴趣" maxLength={1000} value={interest} onChange={e=>setInterest(e.target.value)}/></label>
      <label>算力／数据条件<input aria-label="Idea资源" maxLength={1000} value={resources} onChange={e=>setResources(e.target.value)}/></label></div>
    <p>生成会将所选来源与原文发送到已配置模型，每次任务最多两次请求。已有人工版本保留；新结果为草稿。</p>
    <button disabled={busy || checked.length===0 || !!job && ['pending','running'].includes(job.status)} onClick={()=>void generate()}>根据来源生成 Idea（真实 API）</button>
    {job && <div role="status" className="run-status">Idea生成 · {job.status} · {job.stage} · {job.elapsed===null?'未完成':job.elapsed+' ms'}
      <p>{job.cached?'复用已完成结果，未调用模型':`已知请求：${job.calls??'未完成'}；已返回用量：${job.usage?JSON.stringify(job.usage):'未报告，费用未知'}`}</p>
      {job.error && <p className="error-message">{job.error}</p>}{job.ideaId && <button onClick={()=>void open(job.ideaId??'')}>打开本次 Idea</button>}</div>}
    {error && <p className="error-message" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <h3>{selected?'编辑待验证设想':'新增待验证设想'}</h3>
    {selected && <><p>初始来源：{root?.generation_run_id?'真实 API 生成':root?.is_user_edited?'人工新增':'未记录'}；当前标记：{versionLabel(selected)}。</p>
      <label>Idea版本<select aria-label="Idea版本" value={selected.id} onChange={e=>void open(e.target.value)}>{versions.map(v=><option key={v.id} value={v.id}>v{v.version} · {states[v.status]} · {versionLabel(v)}</option>)}</select></label>
      <ExportActions key={selected.id} ideaIds={[selected.id]} disabled={dirty} />
      {!selected.experiment && <p className="configuration-warning">旧记录没有结构化对照实验，须补充后保存；原文字计划：{selected.minimum_experiment||'未填写'}</p>}</>}
    <p>以下是拟议实验，尚未执行。两组共用模型、数据集和划分，固定设置仅改变指定因素；该结构检查不等于充分的因果验证。</p>
    {Object.entries(labels).map(([key,label])=><label key={key}>{label}<textarea aria-label={'Idea '+key} disabled={busy} maxLength={key==='novelty_checks'||key==='pending'?4000:1500} value={form[key as keyof Form]}
      onChange={e=>{const text=e.target.value;setForm(current=>({...current,[key]:text}));setDirty(true)}}/></label>)}
    <label>Idea状态<select aria-label="Idea状态" disabled={busy} value={status} onChange={e=>{setStatus(e.target.value);setDirty(true)}}>{Object.entries(states).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <button disabled={busy || checked.length===0 || !dirty} onClick={()=>void save()}>保存 Idea（新版本）</button>
    {dirty && <p>有未保存编辑；生成和列表刷新不会替换当前内容。</p>}
    {selected && <><h3>本版本保存的来源</h3>{selected.sources.length===0 && <p>本版本没有保存来源，请选择已支持条目后另存新版本。</p>}{selected.sources.map((source,i)=><article key={i}><p>{source.paper_title||'题目未提供'} · 文件v{source.file_version}／笔记v{source.note_version??'未记录'}{source.removed?' · 文献已软移除，来源仍保留':''}</p>
      <p>初始精读来源：{origins[source.source_mode??'']??'未记录'}；{kinds[source.content_type??'']??'类型未记录'}：{source.content??'来源条目内容未记录'}</p>{source.warning && <p role="alert">{source.warning}</p>}
      <button onClick={()=>void locate(source)}>定位来源原文</button></article>)}</>}
    {reader && <><h3>来源原文 · 文件v{reader.source.file_version}／笔记v{reader.source.note_version??'未记录'}</h3><p>{reader.source.content??'未保存具体条目'}</p>
      <Reader key={reader.source.document_id} documentId={reader.source.document_id} selected={reader.evidence}/></>}
  </section>
}
