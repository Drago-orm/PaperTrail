"""Freeze saved versions once; render both formats from the same ordered content."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
from html import escape
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Literal
from uuid import UUID, uuid4

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app import db
from app.config import Settings
from app.schemas import AnalysisItem, utc_now
from app.services.ideas import from_row as idea_from_row
from app.services.reading_runs import note_from_row
from app.services.pdf_import import ParseReport


KINDS = {'author_statement': '作者陈述', 'system_analysis': '系统判断', 'research_idea': '待验证设想'}
CHECKS = {'unchecked': '仍待核查', 'supported': '支持', 'partially_supported': '部分支持',
          'unsupported': '不支持', 'not_reported': '本次范围未报告'}
ORIGINS = {'live': '真实 API', 'replay': '样例回放（未调用模型）', 'unknown': '来源未知'}
STATES = {'draft': '草稿', 'confirmed': '用户已确认', 'testing': '验证中', 'archived': '已归档'}
FIELDS = {'motivation': '动机', 'goal': '目标', 'method': '方法', 'dataset': '数据集',
          'baseline': '基线', 'metric': '指标', 'ablation': '消融', 'result': '结果', 'limitation': '局限',
          'problem_introduction': '问题引入', 'contribution_organization': '贡献组织',
          'method_explanation': '方法说明', 'experiment_arrangement': '实验安排', 'limitation_discussion': '局限讨论'}


@dataclass
class Block:
    kind: Literal['heading', 'paragraph', 'table']
    text: str = ''
    level: int = 1
    rows: list[list[str]] = field(default_factory=list)

    def __post_init__(self):
        # Word treats CRLF as two breaks; canonicalize once before either renderer.
        self.text = self.text.replace('\r\n', '\n').replace('\r', '\n')
        self.rows = [[cell.replace('\r\n', '\n').replace('\r', '\n') for cell in row] for row in self.rows]


@dataclass
class Snapshot:
    id: str
    created_at: str
    note_id: str | None
    idea_ids: list[str]
    blocks: list[Block] = field(default_factory=list)

    def heading(self, text: str, level: int = 1):
        self.blocks.append(Block('heading', text, level))

    def paragraph(self, text: str):
        self.blocks.append(Block('paragraph', text))

    def value(self, label: str, value):
        if isinstance(value, list):
            value = '；'.join(str(item) for item in value)
        self.paragraph(f'{label}：{value if value is not None and value != "" else "未提供"}')

    def table(self, rows: list[list[str]]):
        self.blocks.append(Block('table', rows=rows))


def capture(settings: Settings, note_id: UUID | None, idea_ids: list[UUID]) -> Snapshot:
    report = Snapshot(str(uuid4()), utc_now().isoformat(), str(note_id) if note_id else None,
                      [str(key) for key in idea_ids])
    report.heading('论文研读笔记' if note_id else '研究设想与验证计划', 0)
    report.paragraph('本文件为已保存版本的导出快照，未调用模型。自动核查不保证准确；未报告仅指当次输入范围。'
                     '研究设想及实验尚未验证，未执行外部查新，不承诺新颖性、效果或发表价值。')
    documents: dict[str, str] = {}
    evidence: dict[str, tuple[str, str]] = {}
    with db.transaction(settings.db_path) as conn:
        def document(key: str) -> str:
            if key not in documents:
                documents[key] = f'文献 {len(documents)+1}'
            return documents[key]

        def source(key: str, document_id: str) -> str:
            pair = evidence.get(key)
            if pair and pair[1] != document_id:
                raise ValueError('证据关联了不同文件版本，不能导出')
            if pair is None:
                evidence[key] = (f'证据 {len(evidence)+1}', document_id)
            return evidence[key][0]

        def run_info(key, label: str):
            report.value(label, key)
            row = conn.execute('SELECT status,model_name,prompt_version,parse_version FROM runs WHERE id=?', (str(key),)).fetchone() if key else None
            if row:
                for name, title in [('status', '任务状态'), ('model_name', '模型'), ('prompt_version', 'Prompt版本'), ('parse_version', '任务解析版本')]:
                    report.value(title, row[name])
            else:
                report.paragraph('任务溯源未记录；不能据此判定已通过核查或来自模型。')

        def note_info(note):
            report.value('笔记版本', f'v{note.version} / {note.id}')
            report.value('父笔记', note.parent_note_id)
            report.value('保存时间', note.created_at.isoformat())
            report.value('人工修订', '是' if note.is_user_edited else '否')
            report.value('初始精读来源', ORIGINS[note.source_mode])
            report.value('所属文件', document(str(note.document_id)))
            run_info(note.verification_run_id, '核查任务')

        def item_info(item: AnalysisItem, doc_id: str):
            report.value('条目ID', item.id)
            report.value('内容标签', KINDS[item.content_type])
            report.value('核查状态', CHECKS[item.verification_status])
            report.value('核查理由', item.verification_reason)
            report.value('保存正文', item.content)
            keys = list(dict.fromkeys([str(key) for key in item.evidence_ids] + [str(q.evidence_id) for q in item.quotes]))
            report.value('原文定位', [source(key, doc_id) for key in keys])
            for quote in item.quotes:
                report.value(f'逐字引文 / {source(str(quote.evidence_id), doc_id)}', quote.text)
            if not item.quotes:
                report.paragraph('未保存逐字引文；证据原文见出处附录，不补造引用。')

        if note_id:
            row = conn.execute('SELECT * FROM notes WHERE id=?', (str(note_id),)).fetchone()
            if row is None:
                raise LookupError('笔记版本不存在')
            note = note_from_row(row)
            report.heading('笔记版本')
            note_info(note)
            entries = note.items + note.writing_items
            report.table([['条目', '内容标签', '核查状态']] + [
                [FIELDS.get(item.field, item.field), KINDS[item.content_type], CHECKS[item.verification_status]] for item in entries])
            for label, items in [('精读条目', note.items), ('写作分析', note.writing_items)]:
                report.heading(label)
                if not items:
                    report.paragraph('此版本未保存该类条目。')
                for index, item in enumerate(items, 1):
                    report.heading(f'{index} {FIELDS.get(item.field, item.field)}', 2)
                    item_info(item, str(note.document_id))

        for index, key in enumerate(idea_ids, 1):
            row = conn.execute('SELECT * FROM ideas WHERE id=?', (str(key),)).fetchone()
            if row is None:
                raise LookupError('Idea版本不存在')
            idea = idea_from_row(row)
            report.heading(f'设想 {index}')
            for label, value in [('Idea版本', f'v{idea.version} / {idea.id}'), ('根版本', idea.root_id),
                                 ('父版本', idea.parent_idea_id), ('保存时间', idea.created_at.isoformat()),
                                 ('人工修订或新增', '是' if idea.is_user_edited else '否'), ('状态', STATES[idea.status])]:
                report.value(label, value)
            run_info(idea.generation_run_id, '生成任务')
            for label, name in [('研究问题', 'question'), ('待验证假设', 'hypothesis'), ('来源推导', 'derivation'),
                                ('与原作的拟议差异', 'difference'), ('原文字实验计划', 'minimum_experiment'),
                                ('所需资源与条件', 'resources'), ('失败条件', 'failure_conditions'), ('待核实的新颖性问题', 'novelty_checks')]:
                report.value(label, getattr(idea, name))
            report.heading('最小对照实验', 2)
            experiment = idea.experiment
            if experiment:
                rows = [['项目', '对照组', '干预组']]
                rows += [[label, getattr(experiment.control, name), getattr(experiment.treatment, name)]
                         for label, name in [('模型', 'model'), ('数据集', 'dataset'), ('划分', 'split')]]
                rows += [[name, value, experiment.treatment.settings[name]] for name, value in experiment.control.settings.items()]
                report.table(rows)
                report.value('唯一变化因素', experiment.changed_factor)
                report.value('评估指标', experiment.metric)
                report.value('统一评估协议', experiment.evaluation)
                report.value('待确认条件', experiment.pending_confirmations)
            else:
                report.paragraph('旧记录没有结构化实验；仅保留原文字计划，不能声称满足单因素约束。')
            report.heading('本版本来源', 2)
            sources = conn.execute('SELECT * FROM idea_sources WHERE idea_id=? ORDER BY rowid', (str(key),)).fetchall()
            if not sources:
                report.paragraph('本版本没有保存来源，无法定位，不补造。')
            seen = set()
            for origin in sources:
                doc_id = origin['document_id']
                report.value('来源文件', document(doc_id))
                if origin['evidence_id']:
                    report.value('来源证据', source(origin['evidence_id'], doc_id))
                pair = (origin['note_id'], origin['item_id'])
                if pair in seen:
                    continue
                seen.add(pair)
                saved = conn.execute('SELECT * FROM notes WHERE id=?', (origin['note_id'],)).fetchone()
                if saved is None:
                    report.paragraph('来源笔记未记录；具体条目与核查状态未知。')
                    continue
                note = note_from_row(saved)
                note_info(note)
                item = next((v for v in note.items + note.writing_items if str(v.id) == origin['item_id']), None)
                if item:
                    item_info(item, doc_id)
                else:
                    report.paragraph('旧来源未保存具体条目，无法提供条目内容或逐字引文。')

        report.heading('离线出处与解析范围')
        if not documents:
            report.paragraph('没有保存可定位文件来源。')
        for key, label in documents.items():
            report.heading(label, 2)
            row = conn.execute('SELECT * FROM documents WHERE id=?', (key,)).fetchone()
            if row is None:
                report.value('文件缺失', key)
                continue
            paper = conn.execute('SELECT * FROM papers WHERE id=?', (row['paper_id'],)).fetchone()
            for name, title in [('title', '论文标题'), ('authors', '作者'), ('abstract', '摘要'), ('keywords', '关键词'),
                                ('tags', '标签'), ('tasks', '任务'), ('methods', '方法'), ('datasets', '数据集'), ('doi', 'DOI'), ('year', '出版年份'), ('reading_status', '阅读状态')]:
                value = paper[name]
                report.value(title, json.loads(value) if name in {'authors', 'keywords', 'tags', 'tasks', 'methods', 'datasets'} else value)
            report.value('文献ID', row['paper_id'])
            report.value('文献入库时间', paper['created_at'])
            report.value('文献已软移除', '是（来源保留）' if paper['removed'] else '否')
            owner = conn.execute('SELECT library_id FROM paper_owners WHERE original_id=?', (row['paper_id'],)).fetchone()[0]
            if owner != row['paper_id']:
                report.value('当前汇总主条目ID（原归属保留）', owner)
            for name, title in [('original_name', '原文件名'), ('id', '文件ID'), ('version', '文件版本'),
                                ('sha256', '文件SHA256'), ('created_at', '文件入库时间'), ('page_count', '物理总页数'), ('parse_status', '解析状态'), ('parse_version', '解析版本')]:
                report.value(title, row[name])
            report.value('原PDF当前可用', '是' if (settings.data_dir / row['storage_path']).is_file() else '否（以下仅保留数据库中已保存的出处）')
            saved = conn.execute('SELECT report_json FROM parse_reports WHERE document_id=?', (key,)).fetchone()
            if saved:
                coverage = ParseReport.model_validate_json(saved[0])
                report.value('解析覆盖状态', coverage.status)
                report.value('解析范围说明', coverage.scope)
                report.value('报告解析版本', coverage.parser_version)
                report.value('报告物理总页数', coverage.page_count)
                report.value('已提取物理页', coverage.extracted_pages)
                report.value('解析警告', coverage.warnings or ['未记录警告'])
                report.value('解析错误', coverage.error or '未记录错误')
                report.value('元数据候选（未确认）', json.dumps(coverage.metadata_candidates, ensure_ascii=False))
            else:
                report.paragraph('解析覆盖报告缺失，覆盖范围与警告未知。')

        for key, (label, doc_id) in evidence.items():
            report.heading(label, 2)
            report.value('文件出处', documents[doc_id])
            report.value('证据ID', key)
            row = conn.execute('SELECT * FROM evidence_blocks WHERE id=? AND document_id=?', (key, doc_id)).fetchone()
            if row is None:
                report.paragraph('证据缺失或不属于所指文件，无法提供原文与页码。')
                continue
            for name, title in [('physical_page', '物理页码（从1开始）'), ('printed_page', '印刷页码'), ('section', '章节候选'), ('paragraph_index', '段落序号')]:
                report.value(title, row[name])
            report.value('区域坐标（PDF点，左上角原点）', [row[name] for name in ('x0', 'top', 'x1', 'bottom')])
            report.value('页面尺寸与旋转', [row[name] for name in ('page_width', 'page_height', 'rotation')])
            report.value('保存原文', row['text'])
    return report


def markdown_text(text: str) -> str:
    # Exported paper text is literal content, never Markdown/HTML instructions.
    value = escape(text.replace('\r\n', '\n').replace('\r', '\n'), quote=False)
    value = re.sub(r'([\\`*_{}\[\]#|~])', r'\\\1', value)
    value = re.sub(r'(?m)^(\s*)([-+=])', r'\1\\\2', value)
    return re.sub(r'(?m)^(\s*\d+)\.(?=\s)', r'\1\\.', value)


def render_markdown(blocks: list[Block]) -> bytes:
    parts = []
    for block in blocks:
        if block.kind == 'table':
            rows = ['| ' + ' | '.join(markdown_text(cell).replace('\n', '<br>') for cell in row) + ' |' for row in block.rows]
            rows.insert(1, '| ' + ' | '.join('---' for _ in block.rows[0]) + ' |')
            parts.append('\n'.join(rows))
        else:
            prefix = '#' * (block.level + 1) + ' ' if block.kind == 'heading' else ''
            parts.append(prefix + markdown_text(block.text).replace('\n', '  \n'))
    return ('\n\n'.join(parts) + '\n').encode('utf-8')


def render_word(blocks: list[Block], created_at: str) -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Cm(2.3)
    for name, size in [('Normal', 11), ('Title', 22), ('Heading 1', 16), ('Heading 2', 13)]:
        style = doc.styles[name]
        style.font.name, style.font.size, style.font.color.rgb = 'Calibri', Pt(size), RGBColor(0, 0, 0)
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), 'Microsoft YaHei')
        style.paragraph_format.space_after = Pt(7)
        for border in style.element.xpath('./w:pPr/w:pBdr'):
            border.getparent().remove(border)
    doc.styles['Normal'].paragraph_format.line_spacing = 1.15
    doc.core_properties.title = blocks[0].text
    doc.core_properties.author = 'PaperTrail'
    doc.core_properties.created = doc.core_properties.modified = datetime.fromisoformat(created_at)
    for block in blocks:
        if block.kind == 'heading':
            doc.add_paragraph(block.text, style='Title' if block.level == 0 else f'Heading {block.level}')
        elif block.kind == 'paragraph':
            doc.add_paragraph(block.text)
        else:
            count = len(block.rows[0])
            table = doc.add_table(rows=0, cols=count)
            table.autofit = False
            widths = [Cm(3.4)] + [Cm(13 / (count-1))] * (count-1)
            for column, width in zip(table.columns, widths):
                column.width = width
            borders = OxmlElement('w:tblBorders')
            for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
                border = OxmlElement(f'w:{edge}')
                for key, value in [('val', 'single'), ('sz', '4'), ('color', 'D9D9D9')]:
                    border.set(qn(f'w:{key}'), value)
                borders.append(border)
            table._tbl.tblPr.append(borders)
            margins = OxmlElement('w:tblCellMar')
            for edge in ('top', 'bottom', 'left', 'right'):
                item = OxmlElement(f'w:{edge}')
                item.set(qn('w:w'), '100')
                item.set(qn('w:type'), 'dxa')
                margins.append(item)
            table._tbl.tblPr.append(margins)
            for index, values in enumerate(block.rows):
                row = table.add_row()
                if index == 0:
                    row._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
                for cell, width, value in zip(row.cells, widths, values):
                    cell.width, cell.text = width, value
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.keep_with_next = False
                        paragraph.paragraph_format.space_after = Pt(3)
                    if index == 0:
                        shade = OxmlElement('w:shd')
                        shade.set(qn('w:fill'), 'E8EEF4')
                        cell._tc.get_or_add_tcPr().append(shade)
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def create(settings: Settings, note_id: UUID | None, idea_ids: list[UUID]) -> dict:
    snapshot = capture(settings, note_id, idea_ids)
    # XML 1.0 cannot represent these characters; fail both formats rather than silently lose text.
    for block in snapshot.blocks:
        for text in [block.text, *(cell for row in block.rows for cell in row)]:
            if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', text):
                raise ValueError('保存内容包含 Word 不支持的控制字符，未生成导出，请修订后重试')
    content = json.dumps(asdict(snapshot), ensure_ascii=False, sort_keys=True).encode('utf-8')
    digest = sha256(content).hexdigest()
    header = [snapshot.blocks[0], Block('paragraph', f'导出快照：{snapshot.id}'),
              Block('paragraph', f'快照时间：{snapshot.created_at}'), Block('paragraph', f'内容SHA256：{digest}')]
    blocks = header + snapshot.blocks[1:]
    files = {'md': render_markdown(blocks), 'docx': render_word(blocks, snapshot.created_at)}
    directory = settings.data_dir / 'exports' / snapshot.id
    directory.mkdir(parents=True)
    try:
        for format, data in files.items():
            (directory / f'report.{format}').write_bytes(data)
        (directory / 'snapshot.json').write_bytes(content)
        # A manifest written last marks a complete pair; partial exports cannot be downloaded.
        (directory / 'manifest.json').write_text(json.dumps({format: sha256(data).hexdigest() for format, data in files.items()}), encoding='utf-8')
    except OSError:
        for name in ('report.md', 'report.docx', 'snapshot.json', 'manifest.json'):
            (directory / name).unlink(missing_ok=True)
        directory.rmdir()
        raise
    return {'id': snapshot.id, 'created_at': snapshot.created_at, 'content_sha256': digest,
            'markdown_url': f'/api/exports/{snapshot.id}/md', 'word_url': f'/api/exports/{snapshot.id}/docx'}


def download(settings: Settings, export_id: UUID, format: Literal['md', 'docx']) -> Path:
    root = (settings.data_dir / 'exports').resolve()
    directory = root / str(export_id)
    path = directory / f'report.{format}'
    for candidate in (directory, path, directory / 'manifest.json'):
        if not candidate.resolve().is_relative_to(root):
            raise ValueError('导出文件路径超出数据目录')
    if not (directory / 'manifest.json').is_file() or not path.is_file():
        raise LookupError('导出快照不存在或生成未完成，请重新准备导出')
    try:
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        if manifest[format] != sha256(path.read_bytes()).hexdigest():
            raise ValueError('导出文件完整性检查失败，请重新准备导出')
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError('导出清单损坏，请重新准备导出') from exc
    return path
