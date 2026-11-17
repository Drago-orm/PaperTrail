"""Compare saved notes and literal evidence excerpts, without ranking or model calls."""
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app import db
from app.agents.reading import check_quotes
from app.schemas import EvidenceQuote, Schema, utc_now
from app.services.pdf_import import ParseReport
from app.services.reading_runs import note_from_row
from app.services.retrieval import context
from app.services.storage import document_path


MATRIX = {'goal': '任务与目标', 'method': '方法', 'dataset': '数据集', 'baseline': '基线',
          'metric': '指标', 'ablation': '消融', 'limitation': '局限'}
CONDITIONS = {'task': '任务', 'dataset': '数据集', 'split': '数据划分', 'metric': '指标定义与单位',
              'protocol': '评估协议', 'training': '训练配置', 'inference': '推理配置'}
READINESS = {'code': '代码', 'data': '数据获取', 'dependencies': '依赖与环境', 'parameters': '参数',
             'training': '训练配置', 'inference': '推理配置', 'hardware': '硬件'}
LABELS = {
    'task': ('task', '任务'), 'dataset': ('dataset', '数据集'), 'split': ('data split', 'dataset split', '数据划分'),
    'metric': ('metric', '指标定义', '指标'), 'protocol': ('evaluation protocol', '评估协议'),
    'code': ('code', 'source code', '代码'), 'data': ('data availability', 'data access', '数据获取'),
    'dependencies': ('dependencies', 'environment', '依赖', '环境'), 'parameters': ('parameters', 'hyperparameters', '参数', '超参数'),
    'training': ('training configuration', 'training config', '训练配置'),
    'inference': ('inference configuration', 'inference config', '推理配置'), 'hardware': ('hardware', '硬件'),
}
PATTERNS = {key: re.compile(r'^\s*(?:'+ '|'.join(re.escape(label) for label in labels) + r')\s*[:：]\s*(\S.*)$', re.I)
            for key, labels in LABELS.items()}
MISSING = re.compile(r'^(?:未提供|未报告|未公布|未公开|未发布|尚未开源|不公开|暂不可用|not\s+(?:reported|provided|available|released|public)|unavailable|none|n/a)[。.!]?$', re.I)


class Annotation(EvidenceQuote):
    field: str
    value: str = Field(min_length=1, max_length=600)

    @model_validator(mode='after')
    def literal(self):
        if self.field not in LABELS or not self.value.strip() or self.value not in self.text:
            raise ValueError('摘录字段无效，或值不是逐字引文的一部分')
        return self


class Selection(Schema):
    note_id: UUID
    annotations: list[Annotation] = Field(default_factory=list, max_length=12)

    @model_validator(mode='after')
    def unique_fields(self):
        if len({item.field for item in self.annotations}) != len(self.annotations):
            raise ValueError('同一笔记的每个字段最多补充一次人工摘录')
        return self


class ComparisonRequest(Schema):
    studies: list[Selection] = Field(min_length=2, max_length=5)

    @model_validator(mode='after')
    def unique_notes(self):
        if len({study.note_id for study in self.studies}) != len(self.studies):
            raise ValueError('请选择2—5个不重复的笔记版本')
        return self


def normalized(value: str) -> str:
    # Preserve punctuation, numbers and units; text equality is never semantic comparability.
    return ' '.join(value.split())


def excerpt_state(entries: list[dict]) -> dict:
    usable = [entry for entry in entries if not MISSING.fullmatch(entry['value'].strip())]
    values = {normalized(entry['value']) for entry in usable}
    if not entries:
        return {'status': 'not_reported', 'value': None, 'entries': [], 'warning': '本次有限原文范围未识别到线索；不代表全文未报告。'}
    if not usable:
        return {'status': 'not_provided', 'value': None, 'entries': entries, 'warning': '摘录表明未提供或不可用，未计为已就绪；请核对原文语境。'}
    if len(values) > 1 or len(usable) != len(entries):
        return {'status': 'ambiguous', 'value': None, 'entries': entries, 'warning': '存在不同值或可用性表述冲突，可能涉及多套实验；未自动选取一套。'}
    return {'status': 'clue', 'value': usable[0]['value'], 'entries': entries,
            'warning': '仅原文线索／人工摘录，尚未核实完整性、适用范围或资源可访问性；不是复现就绪结论。'}


def build(settings, request: ComparisonRequest) -> dict:
    studies, owners = [], set()
    with db.transaction(settings.db_path) as conn:
        for selection in request.studies:
            row = conn.execute('SELECT * FROM notes WHERE id=?', (str(selection.note_id),)).fetchone()
            if row is None:
                raise LookupError('所选笔记版本不存在')
            note = note_from_row(row)
            doc = conn.execute('SELECT * FROM documents WHERE id=?', (str(note.document_id),)).fetchone()
            owner = conn.execute('SELECT library_id FROM paper_owners WHERE original_id=?', (str(note.paper_id),)).fetchone()[0]
            if owner in owners:
                raise ValueError('同一文献或已合并汇总组只能选择一个文件／笔记版本；不同版本不能冒充多篇独立文献')
            owners.add(owner)
            paper = conn.execute('SELECT * FROM papers WHERE id=?', (str(note.paper_id),)).fetchone()
            library = conn.execute('SELECT title,removed FROM papers WHERE id=?', (owner,)).fetchone()
            saved = conn.execute('SELECT report_json FROM parse_reports WHERE document_id=?', (str(note.document_id),)).fetchone()
            coverage = ParseReport.model_validate_json(saved[0]).model_dump() if saved else None
            entries = [item for item in note.items if item.field in MATRIX]
            anchors = list(dict.fromkeys([key for item in entries for key in item.evidence_ids] +
                [q.evidence_id for item in entries for q in item.quotes] + [q.evidence_id for q in selection.annotations]))
            blocks = context(settings.db_path, note.document_id, anchors)
            originals = {block.id: block.text for block in blocks}
            truncated = [str(block.id) for block in blocks if len(block.text) != conn.execute(
                'SELECT length(text) FROM evidence_blocks WHERE id=?', (str(block.id),)).fetchone()[0]]
            warnings = ['原文范围最多40块／16000字符；标签匹配不等于理解全文，未识别项可补充有出处的人工摘录。']
            if coverage is None:
                warnings.append('解析覆盖报告缺失，覆盖范围未知。')
            elif coverage['status'] != 'parsed':
                warnings.append('解析受限或失败，请结合保存的覆盖报告核对遗漏。')
            if note.verification_run_id is None:
                warnings.append('未保存核查任务，笔记的支持标记不能视为已完成真实核查。')
            else:
                run = conn.execute('SELECT status,parse_version FROM runs WHERE id=?', (str(note.verification_run_id),)).fetchone()
                if run is None or run['status'] != 'succeeded' or run['parse_version'] != doc['parse_version']:
                    warnings.append('核查任务缺失、未成功或解析版本已不同，不能将保存标记视为当前已核查。')
            if library['removed']:
                warnings.append('所属文献组已软移除；仍按指定的原始笔记版本比较。')
            if truncated:
                warnings.append('部分相邻证据被范围上限截短，未从截短块自动匹配配置标签。')
            available = True
            try:
                document_path(settings.db_path, settings.data_dir, note.document_id)
            except FileNotFoundError:
                available = False
                warnings.append('原PDF缺失；保留数据库原文出处，当前无法打开文件。')
            cells = {}
            for field in MATRIX:
                values, problems = [], []
                for item in (item for item in entries if item.field == field):
                    if item.verification_status in {'not_reported', 'unsupported'} or item.content_type == 'research_idea':
                        problems.append('此条目在该笔记输入范围未报告，留空。' if item.verification_status == 'not_reported' else
                                        '保存的核查状态为不支持，不作为对比结论。' if item.verification_status == 'unsupported' else
                                        '研究设想不是已报告结果，不作为对比结论。')
                        continue
                    try:
                        check_quotes(item.evidence_ids, item.quotes, originals, required=True)
                    except ValueError as exc:
                        problems.append(f'{item.id}：{exc}，未纳入对比。')
                        continue
                    values.append(item.model_dump(mode='json'))
                cells[field] = {'items': values, 'warnings': problems or ([] if values else ['此笔记版本没有可用且带逐字出处的条目，留空。'])}
            excerpts = {key: [] for key in LABELS}
            for block in blocks:
                if str(block.id) in truncated:
                    continue
                for line in block.text.splitlines():
                    for key, pattern in PATTERNS.items():
                        match = pattern.fullmatch(line)
                        if match:
                            value = match.group(1).strip()
                            if len(line) > 1200 or len(value) > 600:
                                warnings.append(f'{key}匹配行过长，未自动摘录；可人工选择短原文。')
                                continue
                            excerpts[key].append({'value': value, 'evidence_id': str(block.id), 'text': line, 'origin': 'label'})
            for annotation in selection.annotations:
                check_quotes([annotation.evidence_id], [annotation], originals, required=True)
                excerpts[annotation.field].append({**annotation.model_dump(mode='json', exclude={'field'}), 'origin': 'manual'})
            studies.append({'note_id': str(note.id), 'note_version': note.version, 'is_user_edited': note.is_user_edited,
                'source_mode': note.source_mode, 'verification_run_id': str(note.verification_run_id) if note.verification_run_id else None,
                'paper_id': str(note.paper_id), 'library_id': owner, 'library_title': library['title'], 'removed': bool(library['removed']),
                'title': paper['title'], 'authors': db.paper_values(paper)['authors'],
                'document_id': str(note.document_id), 'file_version': doc['version'], 'filename': doc['original_name'], 'sha256': doc['sha256'],
                'file_available': available, 'coverage': coverage, 'warnings': warnings, 'cells': cells,
                'conditions': {key: excerpt_state(excerpts[key]) for key in CONDITIONS},
                'readiness': {key: excerpt_state(excerpts[key]) for key in READINESS},
                'evidence': [block.model_dump(mode='json') for block in blocks],
                'scope': {'physical_pages': sorted({block.physical_page for block in blocks}), 'blocks': len(blocks),
                          'chars': sum(len(block.text) for block in blocks), 'truncated_evidence_ids': truncated}})
        pairs = []
        for index, left in enumerate(studies):
            for right in studies[index+1:]:
                checks = []
                for key, label in CONDITIONS.items():
                    a, b = left['conditions'][key], right['conditions'][key]
                    status = 'unknown' if a['status'] != 'clue' or b['status'] != 'clue' else 'text_match' if normalized(a['value']) == normalized(b['value']) else 'different'
                    checks.append({'field': key, 'label': label, 'status': status})
                status = 'different' if any(check['status'] == 'different' for check in checks) else 'unknown' if any(check['status'] == 'unknown' for check in checks) else 'text_match'
                links = [dict(row) for row in conn.execute('''SELECT r.* FROM paper_relations r
                    JOIN paper_owners a ON a.original_id=r.left_id JOIN paper_owners b ON b.original_id=r.right_id
                    WHERE (a.library_id=? AND b.library_id=?) OR (a.library_id=? AND b.library_id=?)''',
                    (left['library_id'], right['library_id'], right['library_id'], left['library_id']))]
                pairs.append({'left_note_id': left['note_id'], 'right_note_id': right['note_id'], 'status': status, 'checks': checks, 'relations': links,
                    'warning': '设置文字不同，不能强行横向比较指标。' if status == 'different' else
                    '条件缺失或有多套设置，可比性未知，不能排名。' if status == 'unknown' else
                    '摘录条件文字一致，仍需人工核对单位、完整协议和未摘录条件；不等于已经可比，不排名。'})
    return {'id': str(uuid4()), 'created_at': utc_now().isoformat(), 'matrix_fields': MATRIX, 'condition_fields': CONDITIONS,
            'readiness_fields': READINESS, 'studies': studies, 'pairs': pairs,
            'warning': '从所选已保存版本生成，未调用模型。每格是有出处的笔记主张，保留人工／回放／核查标记；自动核查不保证准确。'
                       '资源线索不代表已获取、可执行或实验成功；不计算成功率，不排列优劣。'}


def create(settings, request: ComparisonRequest) -> dict:
    report = build(settings, request)
    directory = settings.data_dir / 'comparisons'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report['id']}.json"
    pending = path.with_suffix('.tmp')
    report['content_sha256'] = sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    try:
        pending.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        pending.replace(path)
    except OSError:
        pending.unlink(missing_ok=True)
        raise
    return report


def load(settings, key: UUID) -> dict:
    root = (settings.data_dir / 'comparisons').resolve()
    path = (root / f'{key}.json').resolve()
    if not path.is_relative_to(root):
        raise ValueError('比较快照路径超出数据目录')
    if not path.is_file():
        raise LookupError('比较快照不存在')
    report = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(report, dict) or report.get('id') != str(key) or not isinstance(report.get('studies'), list) or not 2 <= len(report['studies']) <= 5:
        raise ValueError('比较快照格式损坏')
    digest = report.pop('content_sha256', None)
    if digest != sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest():
        raise ValueError('比较快照完整性检查失败')
    report['content_sha256'] = digest
    return report
