import { record } from './libraryApi'

export type Source = { note_id: string | null; item_id: string | null; paper_id: string; document_id: string;
  paper_title: string; removed: boolean; file_version: number; note_version: number | null;
  content: string | null; content_type: string | null; source_mode?: string | null; evidence_id?: string | null; warning?: string | null }
export type Arm = { model: string; dataset: string; split: string; settings: Record<string, string> }
export type Experiment = { control: Arm; treatment: Arm; changed_factor: string; metric: string; evaluation: string; pending_confirmations: string[] }
export type Idea = { id: string; root_id: string; version: number; is_user_edited: boolean; generation_run_id: string | null;
  question: string; hypothesis: string; derivation: string; difference: string; resources: string; failure_conditions: string;
  novelty_checks: string[]; minimum_experiment: string; experiment: Experiment | null; status: string; sources: Source[] }
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(v => typeof v === 'string')
function source(value: unknown): value is Source {
  return record(value) && ['paper_id','document_id','paper_title'].every(k=>typeof value[k]==='string')
    && ['note_id','item_id','content','content_type'].every(k=>value[k]===null || typeof value[k]==='string')
    && typeof value.removed==='boolean' && typeof value.file_version==='number'
    && (value.note_version===null || typeof value.note_version==='number')
}
export function sources(value: unknown): Source[] {
  if (!Array.isArray(value) || !value.every(source)) throw new Error('来源数据格式无效')
  return value
}
function arm(value: unknown): value is Arm {
  return record(value) && ['model','dataset','split'].every(k=>typeof value[k]==='string')
    && record(value.settings) && Object.values(value.settings).every(v=>typeof v==='string')
}
function experiment(value: unknown): value is Experiment {
  return record(value) && arm(value.control) && arm(value.treatment)
    && ['changed_factor','metric','evaluation'].every(k=>typeof value[k]==='string') && strings(value.pending_confirmations)
}
export function idea(value: unknown): Idea {
  if (!record(value) || !['id','root_id','question','hypothesis','derivation','difference','resources','failure_conditions','minimum_experiment','status'].every(k=>typeof value[k]==='string')
    || typeof value.version!=='number' || typeof value.is_user_edited!=='boolean'
    || !(value.generation_run_id===null || typeof value.generation_run_id==='string')
    || !strings(value.novelty_checks) || !(value.experiment===null || experiment(value.experiment))) throw new Error('Idea数据格式无效')
  const origins = sources(value.sources)
  return { id:String(value.id),root_id:String(value.root_id),version:value.version,is_user_edited:value.is_user_edited,
    generation_run_id:value.generation_run_id,question:String(value.question),hypothesis:String(value.hypothesis),derivation:String(value.derivation),
    difference:String(value.difference),resources:String(value.resources),failure_conditions:String(value.failure_conditions),
    minimum_experiment:String(value.minimum_experiment),status:String(value.status),novelty_checks:value.novelty_checks,
    experiment:value.experiment,sources:origins }
}
export function ideas(value: unknown): Idea[] {
  if (!Array.isArray(value)) throw new Error('Idea列表格式无效')
  return value.map(idea)
}
export const sourceKey = (source: Source) => `${source.note_id}:${source.item_id}`
