import { useEffect, useState, type FormEvent } from 'react'
import Reader from './Reader'
import Notes from './Notes'
import Duplicates from './Duplicates'
import { errorMessage, papers, request, result, type Evidence, type Paper, type Result } from './libraryApi'

const emptyFilters = { q: '', title: '', author: '', abstract: '', keyword: '', tag: '', reading_status: '' }
const labels = { q: '关键词', title: '题目', author: '作者', abstract: '摘要', keyword: '论文关键词', tag: '标签' }
const statuses: Record<string, string> = { unread: '未读', reading: '阅读中', read: '已读' }
type Draft = { title: string; authors: string; abstract: string; keywords: string; tags: string; reading_status: string; doi: string; year: string }
const toDraft = (paper:Paper):Draft=>({title:paper.title,authors:paper.authors.join('\n'),abstract:paper.abstract,
  keywords:paper.keywords.join('\n'),tags:paper.tags.join('\n'),reading_status:paper.reading_status,doi:paper.doi,year:paper.year===null?'':String(paper.year)})

export default function Library() {
  const [filters, setFilters] = useState(emptyFilters)
  const [removed, setRemoved] = useState(false)
  const [items, setItems] = useState<Paper[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [selected, setSelected] = useState<Paper | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [documentId, setDocumentId] = useState('')
  const [report, setReport] = useState<Result | null>(null)
  const [reportError, setReportError] = useState('')
  const [evidence, setEvidence] = useState<Evidence | null>(null)

  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    const params = new URLSearchParams({ ...filters, removed: String(removed) })
    void papers(params.toString()).then(value => { if (active) setItems(value) })
      .catch(e => { if (active) setError(errorMessage(e)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [filters, removed, refresh])

  useEffect(() => {
    let active = true
    setReport(null); setEvidence(null); setReportError('')
    if (documentId) void request('/api/documents/' + documentId).then(data => {
      if (active) setReport(result(data))
    }).catch(e => { if (active) setReportError(errorMessage(e)) })
    return () => { active = false }
  }, [documentId])

  function choose(paper: Paper) {
    setSelected(paper)
    setEvidence(null)
    setDraft(toDraft(paper))
    setDocumentId(paper.documents[0]?.id ?? '')
    setNotice('')
  }

  async function upload(file: File) {
    setBusy(true); setError(''); setNotice('')
    try {
      const body = new FormData(); body.append('file', file)
      const data = result(await request('/api/documents', { method: 'POST', body }))
      setFilters(emptyFilters); setRemoved(false)
      const all = await papers('removed=false')
      const owner = all.find(p => p.documents.some(d => d.id === data.document.id))
      if (owner) { choose(owner); setDocumentId(data.document.id) }
      else setSelected(null)
      setRefresh(v => v + 1)
      setNotice(owner ? data.reused ? '检测到相同SHA256，已复用原文件版本；没有创建重复附件。' : '文件已入库，请核对解析覆盖及元数据候选。' : '同文件已在移除区，请在移除区选择恢复。')
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!selected || !draft) return
    setBusy(true); setError(''); setNotice('')
    try {
      const lines = (value: string) => value.split('\n').map(v => v.trim()).filter(Boolean)
      if(draft.year.trim()&&!/^[1-9]\d{3}$/.test(draft.year.trim()))throw Error('年份须为四位整数；未知请留空')
      await request('/api/papers/' + selected.id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...draft, year:draft.year.trim()?Number(draft.year):null, authors: lines(draft.authors), keywords: lines(draft.keywords), tags: lines(draft.tags) }) })
      await refreshSelection(); setNotice('元数据已保存。')
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }

  async function refreshSelection() {
    const all = await papers('removed='+String(removed))
    const owner = all.find(p=>p.documents.some(d=>d.id===documentId))
    // Undo can restore the currently open attachment to a different visibility group.
    const other = documentId&&!owner ? await papers('removed='+String(!removed)) : []
    const fresh = owner ?? other.find(p=>p.documents.some(d=>d.id===documentId)) ?? all.find(p=>p.id===selected?.id)
    setItems(fresh&&fresh.removed!==removed?other:all);setRefresh(v=>v+1)
    if(fresh&&fresh.removed!==removed)setRemoved(fresh.removed)
    if(fresh){setSelected(fresh);setDraft(toDraft(fresh));if(!documentId)setDocumentId(fresh.documents[0]?.id??'')}
    else{setSelected(null);setDraft(null);setDocumentId('')}
  }

  async function changeVisibility(paper: Paper) {
    setBusy(true); setError('')
    try {
      await request(`/api/papers/${paper.id}/visibility?removed=${!paper.removed}`, { method: 'PUT' })
      if (selected?.id === paper.id) { setSelected(null); setDraft(null); setDocumentId('') }
      setRefresh(v => v + 1)
      setNotice(paper.removed ? '所选文献已恢复。' : '所选文献已移至移除区，可恢复；原文件及证据仍保留。')
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }

  return <section className="library-panel" aria-label="文献库">
    <div className="panel-header"><h2>我的文献</h2>
      <label className="upload-button">{busy ? '处理中…' : '上传 PDF'}
        <input aria-label="上传 PDF" type="file" accept="application/pdf,.pdf" disabled={busy} onChange={e => {
          const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ''
        }} /></label>
    </div>
    <div className="library-body">
      <p className="muted">关键词筛选使用文本匹配，不是语义检索。多个条件同时满足才显示。</p>
      <div className="filters">{Object.entries(labels).map(([key, label]) =>
        <label key={key}>{label}<input aria-label={label+'筛选'} value={filters[key as keyof typeof labels]}
          onChange={e => setFilters(v => ({ ...v, [key]: e.target.value }))} /></label>)}
        <label>阅读状态<select aria-label="阅读状态筛选" value={filters.reading_status} onChange={e => setFilters(v => ({ ...v, reading_status: e.target.value }))}>
          <option value="">全部</option>{Object.entries(statuses).map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></label>
      </div>
      <label><input type="checkbox" checked={removed} onChange={e => setRemoved(e.target.checked)} />显示移除区</label>
      <button onClick={() => { setFilters(emptyFilters); setRefresh(v => v + 1) }}>清除筛选 / 刷新</button>
      {error && <p role="alert" className="error-message">{error} <button onClick={() => setRefresh(v => v + 1)}>重试列表</button></p>}
      {notice && <p role="status">{notice}</p>}
      {loading ? <p role="status">正在加载文献…</p> : error ? null : items.length === 0 ? <p>暂无符合条件的文献。</p> :
        <ul className="paper-list">{items.map(paper => <li key={paper.id}>
          <button disabled={busy} onClick={() => choose(paper)}>{paper.title || '题目未提供'}</button>
          <span>{paper.authors.join('、') || '作者未提供'} · {statuses[paper.reading_status]} · {paper.documents.length} 个文件版本</span>
          <button disabled={busy} onClick={() => void changeVisibility(paper)}>{paper.removed ? '恢复' : '移除'}</button>
        </li>)}</ul>}
      {selected && draft && <div className="paper-detail">
        <h2>文献信息</h2>
        <form onSubmit={e => void save(e)}>
          {(['title', 'authors', 'abstract', 'keywords', 'tags'] as const).map(key => <label key={key}>
            {{ title: '题目', authors: '作者（每行一个）', abstract: '摘要', keywords: '关键词（每行一个）', tags: '标签（每行一个）' }[key]}
            <textarea aria-label={'编辑'+key} value={draft[key]} onChange={e => setDraft({ ...draft, [key]: e.target.value })} />
          </label>)}
          <label>DOI（未知留空）<input aria-label="编辑doi" value={draft.doi} maxLength={300} onChange={e=>setDraft({...draft,doi:e.target.value})}/></label>
          <label>出版年份（未知留空）<input aria-label="编辑year" inputMode="numeric" value={draft.year} maxLength={4} onChange={e=>setDraft({...draft,year:e.target.value})}/></label>
          <p>DOI和年份由用户核对；候选匹配只检查DOI形态，不联网验证注册信息。</p>
          <label>阅读状态<select value={draft.reading_status} onChange={e => setDraft({ ...draft, reading_status: e.target.value })}>
            {Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <button disabled={busy} type="submit">保存元数据</button>
        </form>
        <Duplicates key={selected.id} paperId={selected.id} refresh={refresh} disabled={busy||JSON.stringify(draft)!==JSON.stringify(toDraft(selected))} onChanged={refreshSelection}/>
        <label>文件版本<select aria-label="文件版本" value={documentId} onChange={e => { setEvidence(null); setDocumentId(e.target.value) }}>
          {selected.documents.map(doc => <option key={doc.id} value={doc.id}>原文件v{doc.version} · {doc.original_name} · {doc.paper_id===selected.id?'当前条目':'合并来源 '+doc.paper_id.slice(0,8)} · {doc.parse_status}</option>)}</select></label>
        {!documentId && <p>此文献没有原文件。</p>}
        {documentId && <>
          {reportError ? <p role="alert">{reportError}</p> : !report ? <p>正在加载解析报告…</p> : <>
            <p>解析覆盖：{report.report.status} {report.report.error}</p>
            {report.report.warnings.map((warning, i) => <p key={i} className="configuration-warning">{warning}</p>)}
            <p>元数据候选（未确认）：{report.report.metadata_candidates.title ?? '标题未提供'} / {report.report.metadata_candidates.author ?? '作者未提供'}</p>
            {report.report.metadata_candidates.title && <button onClick={() => setDraft({ ...draft, title: report.report.metadata_candidates.title ?? '' })}>采用标题候选</button>}
            {report.report.metadata_candidates.author && <button onClick={() => setDraft({ ...draft, authors: report.report.metadata_candidates.author ?? '' })}>采用作者候选</button>}
          </>}
          <Reader key={documentId} documentId={documentId} selected={evidence} />
          <Notes key={'notes-'+documentId} documentId={documentId} onEvidence={id => {
            const block = report?.evidence.find(b => b.id === id)
            if (block) setEvidence({ ...block })
            else setReportError('引用证据尚未载入或已缺失，请重新选择文件版本')
          }} />
          <h3>原文证据</h3>
          {report?.evidence.length === 0 && <p>没有可靠证据块，请查看解析限制。</p>}
          <ul className="evidence-list">{report?.evidence.map(block => <li key={block.id}>
            <button data-evidence-id={block.id} onClick={() => setEvidence({ ...block })}>第 {block.physical_page} 页 · {block.text}</button>
          </li>)}</ul>
        </>}
      </div>}
    </div>
  </section>
}
