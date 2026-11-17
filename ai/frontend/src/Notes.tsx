import { useEffect, useRef, useState } from 'react'
import { errorMessage, record, request } from './libraryApi'

type Item = { id: string; field: string; content: string; content_type: string; verification_status: string;
  evidence_ids: string[]; quotes: { evidence_id: string; text: string }[] }
type Note = { id: string; version: number; is_user_edited: boolean; source_mode: string; items: Item[] }
type Run = { id: string; status: string; error: string | null; elapsed_ms: number | null;
  mode: string; stage: string; chars: number | null; pages: number[]; noteId: string | null;
  calls: number | null; usage: Record<string, unknown> | null }
const fields: Record<string, string> = { motivation: '动机', goal: '目标', method: '方法', dataset: '数据集',
  baseline: '基线', metric: '指标', ablation: '消融', result: '结果', limitation: '局限' }
const kinds: Record<string, string> = { author_statement: '作者陈述', system_analysis: '系统判断' }
const origins: Record<string, string> = { live: '真实 API', replay: '样例回放（未调用模型）', unknown: '来源未知' }
const stages: Record<string, string> = { queued: '排队', retrieving: '读取证据', calling_model: '调用模型',
  replaying: '加载固定回放', validating: '校验输出和引用', saved: '已保存新版本', failed: '失败' }
function item(value: unknown): value is Item {
  return record(value) && ['id','field','content','content_type','verification_status'].every(k => typeof value[k] === 'string')
    && Array.isArray(value.evidence_ids) && value.evidence_ids.every(v => typeof v === 'string')
    && Array.isArray(value.quotes) && value.quotes.every(q => record(q) && typeof q.evidence_id === 'string' && typeof q.text === 'string')
}
function note(value: unknown): value is Note {
  return record(value) && typeof value.id === 'string' && typeof value.version === 'number'
    && typeof value.is_user_edited === 'boolean' && typeof value.source_mode === 'string'
    && Array.isArray(value.items) && value.items.every(item)
}
function parseNotes(value: unknown): Note[] {
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
  return { id: value.id, status: value.status, error: value.error, elapsed_ms: value.elapsed_ms,
    mode: scope.mode, stage: scope.stage, chars: typeof scope.context_chars === 'number' ? scope.context_chars : null,
    pages: Array.isArray(scope.physical_pages) && scope.physical_pages.every(v => typeof v === 'number') ? scope.physical_pages : [],
    noteId: typeof output.note_id === 'string' ? output.note_id : null,
    calls: typeof output.api_calls === 'number' ? output.api_calls : null, usage: record(output.usage) ? output.usage : null }
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
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const active = versions.find(v => v.id === activeId)
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
      setDraft(Object.fromEntries(active.items.map(i => [i.id, i.content]))); setDirty(false); editorId.current = active.id
    }
  }, [active])
  useEffect(() => {
    if (!job || !['pending','running'].includes(job.status)) return
    let alive = true
    const timer = window.setTimeout(() => {
      void request('/api/reading-runs/' + job.id).then(data => {
        const next = parseRun(data)
        if (alive) { setJob(next); if (['succeeded','failed'].includes(next.status)) setRefresh(v => v + 1) }
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
    const changes = Object.fromEntries(active.items.filter(i => draft[i.id] !== i.content).map(i => [i.id, draft[i.id]]))
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
    <button onClick={() => setRefresh(v => v + 1)}>刷新笔记和任务</button>
    {error && <p role="alert" className="error-message">{error}</p>}
    {job && <div className="run-status" role="status">
      <p>{job.mode === 'replay' ? '样例回放（未调用模型）' : '真实 API 任务'} · {job.status} · {stages[job.stage] ?? job.stage}</p>
      <p>{job.chars === null ? '尚未读取原文' : `本次读取 ${job.chars} 字符，物理页 ${job.pages.join('、')}`} · 耗时 {job.elapsed_ms === null ? '未完成' : job.elapsed_ms+' ms'}</p>
      <p>{job.mode === 'replay' ? '未调用 API，无调用用量' : `API 请求次数：${job.calls ?? '尚未完成'}；用量：${job.usage ? JSON.stringify(job.usage) : '服务未报告，费用未知'}`}</p>
      {job.error && <p className="error-message">{job.error}</p>}
      {job.noteId && <button onClick={() => selectVersion(job.noteId ?? '')}>打开本次结果</button>}
    </div>}
    {loading ? <p>正在读取笔记…</p> : versions.length === 0 ? <p>暂无精读笔记。</p> : <>
      <label>笔记版本<select aria-label="笔记版本" value={activeId} onChange={e => selectVersion(e.target.value)}>
        {versions.map(v => <option key={v.id} value={v.id}>v{v.version} {v.is_user_edited ? '人工修订' : '分析快照'} · {origins[v.source_mode] ?? '来源未知'}</option>)}</select></label>
      {active && <p className={active.source_mode === 'replay' ? 'configuration-warning' : ''}>当前笔记来源：{origins[active.source_mode] ?? '来源未知'}{active.is_user_edited ? '，含人工修订' : ''}</p>}
      {active?.items.map(entry => <article key={entry.id} className="note-item">
        <h3>{fields[entry.field] ?? entry.field} · {kinds[entry.content_type] ?? entry.content_type}</h3>
        <p>{entry.verification_status === 'not_reported' ? '本次范围未报告' : '待原文核查'}</p>
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
