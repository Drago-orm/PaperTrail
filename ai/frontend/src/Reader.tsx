import { useEffect, useRef, useState } from 'react'
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { highlightBox } from './coordinates'
import { errorMessage, type Evidence } from './libraryApi'

GlobalWorkerOptions.workerSrc = workerUrl

export default function Reader({ documentId, selected }: { documentId: string; selected: Evidence | null }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [scale, setScale] = useState(1)
  const [rotation, setRotation] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)
  const stage = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let active = true
    setPdf(null); setError(''); setBusy(true); setPageNumber(1); setRotation(0)
    const task = getDocument({ url: `/api/documents/${documentId}/file` })
    void task.promise.then(value => { if (active) setPdf(value) }).catch(e => { if (active) { setError(errorMessage(e)); setBusy(false) } })
    return () => { active = false; void task.destroy() }
  }, [documentId])
  useEffect(() => { if (selected) setPageNumber(selected.physical_page) }, [selected])
  useEffect(() => {
    if (!pdf || !stage.current) return
    let cancelled = false
    let render: ReturnType<Awaited<ReturnType<PDFDocumentProxy['getPage']>>['render']> | undefined
    const host = stage.current
    setBusy(true); setError(''); host.replaceChildren()
    async function draw() {
      try {
        const page = await pdf!.getPage(pageNumber)
        if (cancelled) return
        const base = page.getViewport({ scale: 1 })
        const viewport = page.getViewport({ scale, rotation: (page.rotate + rotation) % 360 })
        const canvas = document.createElement('canvas')
        const ratio = window.devicePixelRatio || 1
        canvas.width = Math.ceil(viewport.width * ratio); canvas.height = Math.ceil(viewport.height * ratio)
        canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`
        host.style.width = `${viewport.width}px`; host.style.height = `${viewport.height}px`
        host.append(canvas)
        render = page.render({ canvas, viewport, transform: [ratio, 0, 0, ratio, 0, 0] })
        await render.promise
        if (cancelled) return
        if (selected?.physical_page === pageNumber) {
          if (Math.abs(selected.page_width - base.width) > 1 || Math.abs(selected.page_height - base.height) > 1) {
            throw new Error('证据页面尺寸与原文不一致，未显示高亮')
          }
          const box = highlightBox(selected.bbox, base, viewport)
          const marker = document.createElement('div')
          marker.className = 'evidence-highlight'; marker.dataset.evidenceId = selected.id
          Object.assign(marker.style, Object.fromEntries(Object.entries(box).map(([key, value]) => [key, `${value}px`])))
          host.append(marker)
          marker.scrollIntoView({ block: 'nearest', inline: 'nearest' })
        }
        setBusy(false)
      } catch (e) { if (!cancelled) { setError(errorMessage(e)); setBusy(false) } }
    }
    void draw()
    return () => { cancelled = true; render?.cancel() }
  }, [pdf, pageNumber, scale, rotation, selected])
  return <section aria-label="PDF 阅读器">
    <div className="reader-controls">
      <label>物理页 <input type="number" min={1} max={pdf?.numPages ?? 1} value={pageNumber}
        onChange={e => setPageNumber(Math.max(1, Math.min(pdf?.numPages ?? 1, Number(e.target.value) || 1)))} /></label>
      <span>/ {pdf?.numPages ?? '未知'}</span>
      <label>缩放 <select value={scale} onChange={e => setScale(Number(e.target.value))}>
        {[.5, 1, 1.5, 2].map(v => <option key={v} value={v}>{v * 100}%</option>)}</select></label>
      <button onClick={() => setRotation(v => (v + 90) % 360)}>旋转 90°</button>
      <a href={`/api/documents/${documentId}/file`} target="_blank" rel="noreferrer">打开原文件</a>
    </div>
    {busy && <p role="status">正在读取 PDF…</p>}
    {error && <p role="alert" className="error-message">{error}</p>}
    <div className="pdf-scroll"><div ref={stage} className="pdf-stage" /></div>
  </section>
}
