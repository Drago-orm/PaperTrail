import { useEffect, useRef, useState } from 'react'
import { errorMessage, record, request } from './libraryApi'
import ExportActions from './ExportActions'

export type Item = { id: string; field: string; content: string; content_type: string; verification_status: string; verification_reason: string;
  evidence_ids: string[]; quotes: { evidence_id: string; text: string }[] }
export type Note = { id: string; version: number; is_user_edited: boolean; source_mode: string; verification_run_id: string | null; items: Item[]; writing_items: Item[] }
type FlowNode = { role: string; status: string; cached: boolean; manual: boolean; pages: number[]; chars: number; version: number | null; error: string | null }
type Check = { item_id: string; status: string; reason: string }
type Round = { attempt: number; phase: string; items: Check[] }
type Run = { id: string; status: string; error: string | null; elapsed_ms: number | null;
  mode: string; stage: string; chars: number | null; pages: number[]; noteId: string | null;
  calls: number | null; usage: Record<string, unknown> | null; role: string;
  rounds: number | null; checks: Round[]; unresolved: number | null; usageComplete: boolean | null;
  nodes: FlowNode[]; canRetry: boolean; knownCalls: number | null; ideaId: string | null; conditions: { interest: string; resources: string } | null }
const fields: Record<string, string> = { motivation: '动机', goal: '目标', method: '方法', dataset: '数据集',
  baseline: '基线', metric: '指标', ablation: '消融', result: '结果', limitation: '局限',
  problem_introduction: '写作：问题引入', contribution_organization: '写作：贡献组织', method_explanation: '写作：方法说明',
  experiment_arrangement: '写作：实验安排', limitation_discussion: '写作：局限讨论' }
const roles: Record<string, string> = { reading: '精读', writing: '写作分析', verification: '原文核查', idea: '待验证 Idea' }
export const kinds: Record<string, string> = { author_statement: '作者陈述', system_analysis: '系统判断' }
export const statuses: Record<string, string> = { unchecked: '仍待核查', supported: '支持', partially_supported: '部分支持', unsupported: '不支持', not_reported: '本次范围未报告' }
export const origins: Record<string, string> = { live: '真实 API', replay: '样例回放（未调用模型）', unknown: '来源未知' }
const stages: Record<string, string> = { queued: '排队', retrieving: '读取证据', calling_model: '调用模型',
  replaying: '加载固定回放', validating: '校验输出和引用', saved: '已保存新版本', failed: '失败',
  rereading: '重新读取原文', verifying: '逐条核查', revising: '定向返工一次', rereading_revision: '重新读取返工依据', rechecking: '复查问题条目',
  reading: '精读节点', writing: '写作分析节点', verification: '核查节点（含受控返工）', idea:'Idea 节点' }
export function item(value: unknown): value is Item {
  return record(value) && ['id','field','content','content_type','verification_status','verification_reason'].every(k => typeof value[k] === 'string')
    && Array.isArray(value.evidence_ids) && value.evidence_ids.every(v => typeof v === 'string')
    && Array.isArray(value.quotes) && value.quotes.every(q => record(q) && typeof q.evidence_id === 'string' && typeof q.text === 'string')
}
function note(value: unknown): value is Note {
  return record(value) && typeof value.id === 'string' && typeof value.version === 'number'
    && typeof value.is_user_edited === 'boolean' && typeof value.source_mode === 'string'
    && (value.verification_run_id === null || typeof value.verification_run_id === 'string')
    && Array.isArray(value.items) && value.items.every(item) && Array.isArray(value.writing_items) && value.writing_items.every(item)
}
export function parseNotes(value: unknown): Note[] {
  if (!Array.isArray(value) || !value.every(note)) throw new Error('笔记版本数据格式无效')
  return value
}
function parseRun(value: unknown): Run {
  if (!record(value) || typeof value.id !== 'string' || typeof value.status !== 'string'
    || !['pending','running','succeeded','failed'].includes(value.status) || !record(value.input_scope)
    || !(value.error === null || typeof value.error === 'string')
    || !(value.elapsed_ms === null || typeof value.elapsed_ms === 'number')) throw new Error('任务状态格式无效')
  const scope = value.input_scope, output = record(value.output) ? value.output : {}
  if (typeof scope.mode !== 'string' || typeof scope.stage !== 'string') throw new Error('任务范围格式无效')
  const checks: Round[] = []
  const nodes: FlowNode[] = []
  if (output.nodes !== undefined) {
    if (!record(output.nodes)) throw new Error('流程节点格式无效')
    for (const [role, node] of Object.entries(output.nodes)) {
      if (!record(node) || typeof node.status !== 'string' || typeof node.cached !== 'boolean'
        || typeof node.context_chars !== 'number' || !Array.isArray(node.physical_pages)
        || !node.physical_pages.every(v => typeof v === 'number')) throw new Error('节点范围或状态格式无效')
      nodes.push({ role, status: node.status, cached: node.cached, manual: node.manual_preserved === true,
        pages: node.physical_pages, chars: node.context_chars, version: typeof node.input_note_version === 'number' ? node.input_note_version : null,
        error: typeof node.error === 'string' ? node.error : null })
    }
  }
  if (output.checks !== undefined) {
    if (!Array.isArray(output.checks)) throw new Error('核查记录格式无效')
    for (const round of output.checks) {
      if (!record(round) || typeof round.attempt !== 'number' || !Array.isArray(round.items)) throw new Error('核查轮次格式无效')
      const items: Check[] = []
      for (const entry of round.items) {
        if (!record(entry) || typeof entry.item_id !== 'string' || typeof entry.status !== 'string' || typeof entry.reason !== 'string') throw new Error('逐条核查记录格式无效')
        items.push({ item_id: entry.item_id, status: entry.status, reason: entry.reason })
      }
      checks.push({ attempt: round.attempt, phase: typeof round.phase === 'string' ? round.phase : 'unknown', items })
    }
  }
  return { id: value.id, status: value.status, error: value.error, elapsed_ms: value.elapsed_ms,
    mode: scope.mode, stage: scope.stage, chars: typeof scope.context_chars === 'number' ? scope.context_chars : null,
    pages: Array.isArray(scope.physical_pages) && scope.physical_pages.every(v => typeof v === 'number') ? scope.physical_pages : [],
    noteId: typeof output.note_id === 'string' ? output.note_id : null,
    calls: typeof output.api_calls === 'number' ? output.api_calls : null, usage: record(output.usage) ? output.usage : null,
    role: typeof scope.role === 'string' ? scope.role : 'reading', checks,
    rounds: typeof output.revision_rounds === 'number' ? output.revision_rounds : null,
    unresolved: Array.isArray(output.unresolved_item_ids) ? output.unresolved_item_ids.length : null,
    usageComplete: typeof output.usage_complete === 'boolean' ? output.usage_complete : null, nodes,
    canRetry: scope.role === 'workflow' && scope.resume_count === 0,
    knownCalls: typeof output.known_api_calls === 'number' ? output.known_api_calls : null,
    ideaId: typeof output.idea_id==='string'?output.idea_id:null,
    conditions: record(scope.conditions) && typeof scope.conditions.interest === 'string' && typeof scope.conditions.resources === 'string'
      ? { interest: scope.conditions.interest, resources: scope.conditions.resources } : null }
}

export default function Notes({ documentId, onEvidence }: { documentId: string; onEvidence: (id: string) => void }) {
  const [versions, setVersions] = useState<Note[]>([])
  const [activeId, setActiveId] = useState('')
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const editorId = useRef('')
  const [job, setJob] = useState<Run | null>(null)
  const [mode, setMode] = useState('live')
  const [section, setSection] = useState('')
  const [query, setQuery] = useState('')
  const [revise, setRevise] = useState(true)
  const [interest, setInterest] = useState('')
  const [resources, setResources] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const active = versions.find(v => v.id === activeId)
  const entries = active ? [...active.items, ...active.writing_items] : []
  useEffect(() => {
    let alive = true
    setLoading(true); setError('')
    void Promise.all([request(`/api/documents/${documentId}/notes`), request(`/api/documents/${documentId}/reading-runs`)])
      .then(([rawNotes, rawRuns]) => {
        const saved = parseNotes(rawNotes)
        if (!Array.isArray(rawRuns)) throw new Error('任务列表格式无效')
        const runs = rawRuns.map(parseRun)
        if (alive) { setVersions(saved); setActiveId(id => id || saved[0]?.id || ''); setJob(runs[0] ?? null) }
      }).catch(e => { if (alive) setError(errorMessage(e)) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [documentId, refresh])
  useEffect(() => {
    if (active && editorId.current !== active.id) {
      setDraft(Object.fromEntries([...active.items, ...active.writing_items].map(i => [i.id, i.content]))); setDirty(false); editorId.current = active.id
    }
  }, [active])
  useEffect(() => {
    if (!job || !['pending','running'].includes(job.status)) return
    let alive = true
    const timer = window.setTimeout(() => {
      void request('/api/reading-runs/' + job.id).then(data => {
        const next = parseRun(data)
        if (alive) { setJob(next); if (next.noteId !== job.noteId || ['succeeded','failed'].includes(next.status)) setRefresh(v => v + 1) }
      }).catch(e => { if (alive) setError(errorMessage(e)) })
    }, 800)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [job])
  function selectVersion(id: string) {
    if (!dirty || window.confirm('切换版本会放弃尚未保存的修改，是否继续？')) setActiveId(id)
  }
  async function start() {
    setBusy(true); setError('')
    try {
      setJob(parseRun(await request(`/api/documents/${documentId}/reading-runs`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode, section, query }) })))
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }
  async function save() {
    if (!active) return
    const changes = Object.fromEntries(entries.filter(i => draft[i.id] !== i.content).map(i => [i.id, draft[i.id]]))
    if (!Object.keys(changes).length) { setDirty(false); return }
    setBusy(true); setError('')
    try {
      const raw = await request(`/api/documents/${documentId}/notes`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_note_id: active.id, changes }) })
      if (!note(raw)) throw new Error('保存后的笔记格式无效')
      setVersions(v => [raw, ...v]); setActiveId(raw.id); setDirty(false)
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }
  async function verify() {
    if (!active) return
    setBusy(true); setError('')
    try {
      setJob(parseRun(await request(`/api/documents/${documentId}/verification-runs`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_note_id: active.id, revise }) })))
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }
  async function workflow(retry = false) {
    setBusy(true); setError('')
    try {
      const path = retry && job ? `/api/workflow-runs/${job.id}/retry` : `/api/documents/${documentId}/workflow-runs`
      setJob(parseRun(await request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: retry ? undefined : JSON.stringify({ section, query, interest, resources, revise }) })))
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }
  return <section className="reading-notes" aria-label="精读笔记">
    <h2>精读与版本笔记</h2>
    <p>每次最多读取 40 个证据块、16000 字符。未报告仅指本次输入范围；程序引用检查不代表语义核查。</p>
    <label>精读模式<select aria-label="精读模式" value={mode} onChange={e => setMode(e.target.value)}>
      <option value="live">真实 API</option><option value="replay">固定样例回放（未调用模型）</option></select></label>
    {mode === 'replay' ? <p className="configuration-warning">样例回放，未调用模型：仅适用于 fixtures/reader.pdf，不代表真实论文分析效果。</p>
      : <p>点击运行后，所选范围原文会发送到已配置的模型服务；每次任务最多两次 API 请求。</p>}
    <div className="filters"><label>章节候选筛选<input aria-label="精读章节" value={section} maxLength={200} onChange={e => setSection(e.target.value)} /></label>
      <label>证据文本筛选<input aria-label="精读关键词" value={query} maxLength={200} onChange={e => setQuery(e.target.value)} /></label></div>
    <button disabled={busy || !!job && ['pending','running'].includes(job.status)} onClick={() => void start()}>运行精读 / 重新运行</button>
    <div className="filters"><label>用户兴趣<input aria-label="流程兴趣" value={interest} maxLength={1000} onChange={e => setInterest(e.target.value)} /></label>
      <label>资源条件<input aria-label="流程资源" value={resources} maxLength={1000} onChange={e => setResources(e.target.value)} /></label></div>
    <p>流程使用真实 API，依次精读、写作分析、原文核查、Idea；先保存当前修改。失败可沿用原输入恢复一次，成功节点复用；含恢复最多 26 次请求。兴趣与资源用于说明设想的适用条件。</p>
    <button disabled={busy || dirty || mode !== 'live' || !!job && ['pending','running'].includes(job.status)} onClick={() => void workflow()}>运行精读→写作→核查→Idea</button>
    <button onClick={() => setRefresh(v => v + 1)}>刷新笔记和任务</button>
    {error && <p role="alert" className="error-message">{error}</p>}
    {job && <div className="run-status" role="status">
      <p>{job.role === 'workflow' ? '论文研读流程（真实 API）' : job.role === 'idea' ? 'Idea 生成（真实 API）' : job.role === 'verification' ? '原文核查（真实 API）' : job.mode === 'replay' ? '样例回放（未调用模型）' : '真实 API 精读'} · {job.status} · {stages[job.stage] ?? job.stage}</p>
      <p>{job.chars === null ? '尚未读取原文' : job.role === 'idea' ? `来源原文共 ${job.chars} 字符，文件与页码见 Idea 来源` : `本次读取 ${job.chars} 字符，物理页 ${job.pages.join('、')}`} · 耗时 {job.elapsed_ms === null ? '未完成' : job.elapsed_ms+' ms'}</p>
      <p>{job.mode === 'replay' ? '未调用 API，无调用用量' : `任务累计 API 请求次数：${job.calls ?? '未完成或中断期间未知'}；用量：${job.usage ? JSON.stringify(job.usage) : '服务未报告，费用未知'}`}</p>
      {job.calls === null && job.knownCalls !== null && <p>已记录请求 {job.knownCalls} 次，中断时未返回的请求数未知。</p>}
      {job.conditions && <p>本次兴趣：{job.conditions.interest || '未提供'}；资源：{job.conditions.resources || '未提供'}</p>}
      {job.nodes.map(node => <details key={node.role}><summary>{roles[node.role] ?? node.role} · {node.status}{node.cached ? ' · 复用已完成结果，本次未调用模型' : node.manual ? ' · 保留人工内容，未调用模型' : ''}</summary>
        <p>输入笔记：{node.version === null ? '无' : `v${node.version}`}；原文物理页 {node.pages.join('、')}，{node.chars} 字符。</p>
        {node.error && <p className="error-message">{node.error}</p>}</details>)}
      {job.role === 'workflow' && job.status === 'failed' && job.canRetry && <button disabled={busy || dirty} onClick={() => void workflow(true)}>重试失败节点（沿用原输入）</button>}
      {job.usageComplete === false && <p>以上仅含服务已返回的用量；失败／重试用量可能缺失，费用未知。</p>}
      {(job.role === 'verification' || job.role === 'workflow' && job.checks.length > 0) && <p>定向返工：{job.rounds ?? 0} / 1 轮；待解决问题：{job.unresolved ?? '尚未完成'}。自动核查不保证准确性。</p>}
      {job.checks.map(round => <details key={round.attempt}><summary>{round.phase === 'initial' ? '初查' : round.phase === 'recheck' ? '复查' : '核查'}记录（批次 {round.attempt}）</summary>
        {round.items.map(check => <p key={check.item_id}>{fields[versions.flatMap(v => [...v.items, ...v.writing_items]).find(i => i.id === check.item_id)?.field ?? ''] ?? '条目'} · {statuses[check.status] ?? check.status}：{check.reason}</p>)}</details>)}
      {job.error && <p className="error-message">{job.error}</p>}
      {job.noteId && <button onClick={() => selectVersion(job.noteId ?? '')}>打开本次结果</button>}
      {job.ideaId && <a href={'#ideas/'+job.ideaId}>打开本次 Idea</a>}
    </div>}
    {loading ? <p>正在读取笔记…</p> : versions.length === 0 ? <p>暂无精读笔记。</p> : <>
      <label>笔记版本<select aria-label="笔记版本" value={activeId} onChange={e => selectVersion(e.target.value)}>
        {versions.map(v => <option key={v.id} value={v.id}>v{v.version} {v.is_user_edited ? '人工修订' : '分析快照'} · {origins[v.source_mode] ?? '来源未知'}</option>)}</select></label>
      {active && <>
        <ExportActions key={active.id} noteId={active.id} disabled={dirty} />
        <p className={active.source_mode === 'replay' ? 'configuration-warning' : ''}>初始精读来源：{origins[active.source_mode] ?? '来源未知'}{active.is_user_edited ? '，含人工修订' : ''}</p>
        {active.verification_run_id && <p>此版本含真实 API 核查记录；自动判断须由用户确认。</p>}
        <label><input aria-label="允许定向返工" type="checkbox" checked={revise && !active.is_user_edited} disabled={active.is_user_edited} onChange={e => setRevise(e.target.checked)} />问题条目最多定向返工一次</label>
        <p>{active.is_user_edited ? '人工修订只核查，不自动改写。' : '核查重新读取原文；返工后只复查问题条目。'} 最多六次 API 请求，结果保存新版本；缺失或不确定项继续显示。</p>
        <button disabled={busy || dirty || !!job && ['pending','running'].includes(job.status)} onClick={() => void verify()}>核查此版本（真实 API）</button>
        {dirty && <p>请先保存修改，再核查当前内容。</p>}
      </>}
      {entries.map(entry => <article key={entry.id} className="note-item">
        <h3>{fields[entry.field] ?? entry.field} · {kinds[entry.content_type] ?? entry.content_type}</h3>
        <p>{draft[entry.id] !== undefined && draft[entry.id] !== entry.content ? '未保存的修改：尚未核查'
          : `${statuses[entry.verification_status] ?? entry.verification_status} · ${entry.verification_reason}`}</p>
        <textarea aria-label={'笔记'+entry.field} value={draft[entry.id] ?? entry.content} maxLength={10000}
          onChange={e => { setDraft({ ...draft, [entry.id]: e.target.value }); setDirty(true) }} />
        {entry.quotes.map((q, i) => <blockquote key={i}><button onClick={() => onEvidence(q.evidence_id)}>查看原文证据</button> {q.text}</blockquote>)}
        {!entry.quotes.length && entry.evidence_ids.map(id => <button key={id} onClick={() => onEvidence(id)}>查看原文证据</button>)}
      </article>)}
      <button disabled={busy || !dirty} onClick={() => void save()}>保存人工修订（新版本）</button>
      {dirty && <p>有尚未保存的修改；任务刷新不会自动替换当前编辑。</p>}
    </>}
  </section>
}
