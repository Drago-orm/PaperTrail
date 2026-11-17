import { useRef, useState } from 'react'
import { errorMessage, record, request } from './libraryApi'

type Prepared = { key: string; id: string; time: string; hash: string; markdown: string; word: string }

export default function ExportActions({ noteId, ideaIds = [], disabled = false }: {
  noteId?: string; ideaIds?: string[]; disabled?: boolean
}) {
  const selection = JSON.stringify({ note_id: noteId ?? null, idea_ids: ideaIds })
  const current = useRef(selection)
  current.current = selection
  const [saved, setSaved] = useState<Prepared | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const ready = saved?.key === selection ? saved : null
  async function prepare() {
    setPending(true); setError(''); setSaved(null)
    const key = selection
    try {
      const value = await request('/api/exports', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key })
      if (!record(value) || typeof value.id !== 'string' || !/^[0-9a-f-]{36}$/.test(value.id)
        || typeof value.created_at !== 'string' || typeof value.content_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(value.content_sha256)
        || value.markdown_url !== `/api/exports/${value.id}/md` || value.word_url !== `/api/exports/${value.id}/docx`) throw new Error('导出响应格式无效')
      if (current.current === key) setSaved({ key, id: value.id, time: value.created_at, hash: value.content_sha256,
        markdown: value.markdown_url as string, word: value.word_url as string })
    } catch (e) { if (current.current === key) setError(errorMessage(e)) }
    finally { setPending(false) }
  }
  return <div className="export-actions" aria-label="版本导出">
    <button disabled={disabled || pending || !noteId && ideaIds.length === 0} onClick={() => void prepare()}>
      {pending ? '正在准备导出…' : noteId ? '准备导出此笔记版本' : `准备导出所选 Idea（${ideaIds.length}）`}
    </button>
    {disabled && <p>请先保存当前编辑，再导出已保存版本。</p>}
    {error && <p role="alert" className="error-message">{error}</p>}
    {ready && !disabled && <div role="status">
      <p>同一快照的两种格式已准备，未调用模型。之后的编辑不会改变这些文件；更新元数据后请重新准备。</p>
      <a href={ready.markdown} download>下载 Markdown</a>{' · '}<a href={ready.word} download>下载 Word</a>
      <p>快照 {ready.id} · {ready.time}</p><details><summary>内容校验值 SHA256</summary>{ready.hash}</details>
    </div>}
  </div>
}
