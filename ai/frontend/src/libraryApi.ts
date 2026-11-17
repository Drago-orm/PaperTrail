export type Document = { id: string; version: number; original_name: string; parse_status: string }
export type Paper = {
  id: string; title: string; authors: string[]; abstract: string; keywords: string[]; tags: string[];
  reading_status: string; removed: boolean; documents: Document[]
}
export type Evidence = {
  id: string; physical_page: number; text: string; bbox: number[]; page_width: number; page_height: number
}
export type Result = { document: Document; evidence: Evidence[]; report: {
  status: string; warnings: string[]; error: string | null; metadata_candidates: Record<string, string | null>
} }
export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(v => typeof v === 'string')
function document(value: unknown): value is Document {
  return record(value) && typeof value.id === 'string' && typeof value.version === 'number'
    && typeof value.original_name === 'string' && typeof value.parse_status === 'string'
}
function paper(value: unknown): value is Paper {
  return record(value) && typeof value.id === 'string' && typeof value.title === 'string'
    && typeof value.abstract === 'string' && strings(value.authors) && strings(value.keywords) && strings(value.tags)
    && typeof value.reading_status === 'string' && typeof value.removed === 'boolean'
    && Array.isArray(value.documents) && value.documents.every(document)
}
function evidence(value: unknown): value is Evidence {
  return record(value) && typeof value.id === 'string' && typeof value.physical_page === 'number'
    && typeof value.text === 'string' && Array.isArray(value.bbox) && value.bbox.length === 4
    && value.bbox.every(v => typeof v === 'number' && Number.isFinite(v))
    && typeof value.page_width === 'number' && typeof value.page_height === 'number'
}
export async function request(url: string, options?: RequestInit): Promise<unknown> {
  const response = await fetch(url, options)
  const data: unknown = await response.json()
  if (!response.ok) {
    if (record(data) && typeof data.detail === 'string') throw new Error(data.detail)
    if (record(data) && Array.isArray(data.detail)) {
      const issues = data.detail.filter(record).filter(value=>typeof value.msg==='string' && Array.isArray(value.loc))
        .slice(0,8).map(value=>`${(value.loc as unknown[]).join('.')}: ${String(value.msg)}`)
      if (issues.length) throw new Error(issues.join('；'))
    }
    throw new Error(`请求失败 HTTP ${response.status}`)
  }
  return data
}
export async function papers(query: string): Promise<Paper[]> {
  const data = await request('/api/papers?' + query)
  if (!Array.isArray(data) || !data.every(paper)) throw new Error('文献列表格式无效')
  return data
}
export function result(data: unknown): Result {
  if (!record(data) || !document(data.document) || !Array.isArray(data.evidence) || !data.evidence.every(evidence)
    || !record(data.report) || typeof data.report.status !== 'string' || !strings(data.report.warnings)
    || !(data.report.error === null || typeof data.report.error === 'string') || !record(data.report.metadata_candidates)
    || !Object.values(data.report.metadata_candidates).every(v => v === null || typeof v === 'string')) {
    throw new Error('解析报告格式无效')
  }
  const candidates: Record<string, string | null> = {}
  for (const [key, value] of Object.entries(data.report.metadata_candidates)) {
    if (value === null || typeof value === 'string') candidates[key] = value
  }
  return { document: data.document, evidence: data.evidence, report: {
    status: data.report.status, warnings: data.report.warnings, error: data.report.error, metadata_candidates: candidates,
  } }
}
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : '未知错误'
