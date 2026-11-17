"""Deterministic text extraction. Paper text is never executed as instructions."""

from contextlib import closing
import hashlib
import re
from threading import Lock
from typing import Literal
from uuid import UUID

import pdfplumber
from pydantic import BaseModel, Field

from app import db
from app.config import Settings
from app.schemas import Document, EvidenceBlock, Paper
from app.services.storage import document_path, store_document

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 200
PARSER_VERSION = f"papertrail-lines-1/pdfplumber-{pdfplumber.__version__}"
IMPORT_LOCK = Lock()


class ImportRejected(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class ParseReport(BaseModel):
    status: Literal['parsed', 'limited', 'failed']
    scope: str = '仅文字行候选；启发式顺序，不保证通用版式或图表解析'
    parser_version: str = PARSER_VERSION
    page_count: int | None = None
    extracted_pages: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata_candidates: dict[str, str | None] = Field(default_factory=dict)


class ImportResult(BaseModel):
    document: Document
    reused: bool
    report: ParseReport
    evidence: list[EvidenceBlock]


def inspect_pdf(stream, document_id: UUID) -> tuple[ParseReport, list[EvidenceBlock]]:
    report = ParseReport(status="failed")
    blocks = []
    try:
        with pdfplumber.open(stream, strict_metadata=True) as pdf:
            report.page_count = len(pdf.pages)
            if not 0 < report.page_count <= MAX_PAGES:
                report.error = f"PDF 页数须为 1—{MAX_PAGES}，未提取"
                if report.page_count == 0:
                    report.page_count = None
                return report, []
            report.metadata_candidates = {
                key.lower(): value.strip() if isinstance(value, str) and value.strip() else None
                for key, value in (("Title", pdf.metadata.get("Title")), ("Author", pdf.metadata.get("Author")))
            }
            for page in pdf.pages:
                number = page.page_number
                if page.rotation:
                    report.warnings.append(f"第 {number} 页旋转，未提取；尚不支持可靠旋转定位")
                    continue
                words = page.extract_words()
                if not words:
                    report.warnings.append(f"第 {number} 页无可提取文字（可能为空白或扫描页）；未执行 OCR")
                    continue
                # A large gap on a shared baseline signals an uncertain column order.
                rows = {}
                for word in words:
                    rows.setdefault(round(word['top'] / 3), []).append(word)
                columns = any(
                    b['x0'] - a['x1'] > page.width * .15
                    for row in rows.values()
                    for a, b in zip(sorted(row, key=lambda w: w['x0']), sorted(row, key=lambda w: w['x0'])[1:])
                )
                middle = page.width / 2
                columns = columns or (
                    sum(word['x1'] < middle for word in words) >= 3
                    and sum(word['x0'] > middle for word in words) >= 3
                    and not any(word['x0'] < middle < word['x1'] for word in words)
                )
                if columns:
                    report.warnings.append(f"第 {number} 页疑似双栏/复杂布局，顺序不可靠，未提取")
                    continue
                if page.images or page.find_tables():
                    report.warnings.append(f"第 {number} 页含图像或表格，仅提取文字，不保证图表内容与顺序")
                section = None
                lines = sorted(page.extract_text_lines(return_chars=False), key=lambda line: (line['top'], line['x0']))
                page_blocks = []
                for index, line in enumerate(lines):
                    text = line['text'].strip()
                    if not text:
                        continue
                    if re.match(r'^(?:\d+(?:\.\d+)*\s+|Abstract\b|Introduction\b|Conclusion\b|References\b)', text, re.I):
                        section = text
                    page_blocks.append(EvidenceBlock(
                        document_id=document_id, physical_page=number, section=section,
                        paragraph_index=index, text=text,
                        bbox=(line['x0'], line['top'], line['x1'], line['bottom']),
                        page_width=page.width, page_height=page.height,
                    ))
                if page_blocks:
                    blocks.extend(page_blocks)
                    report.extracted_pages.append(number)
                page.close()
            report.status = "limited" if report.warnings else "parsed"
            if not blocks:
                report.status = "failed"
                report.error = "没有可可靠定位的文字证据；请查看逐页限制，扫描件需要 OCR"
    except Exception as exc:
        # Parsing errors are persisted and returned, never converted to success.
        report.status = "failed"
        report.error = f"PDF 解析失败（{type(exc).__name__}）；文件可能损坏、加密或含不支持的结构"
        report.extracted_pages = []
        blocks = []
    return report, blocks


def get_result(settings: Settings, document_id: UUID, reused: bool = False) -> ImportResult:
    with closing(db.connect(settings.db_path)) as conn:
        row = conn.execute('SELECT * FROM documents WHERE id=?', (str(document_id),)).fetchone()
        if row is None:
            raise LookupError('文档不存在')
        saved = conn.execute('SELECT report_json FROM parse_reports WHERE document_id=?', (str(document_id),)).fetchone()
        if saved is None:
            raise LookupError('文档尚无解析报告')
        report = ParseReport.model_validate_json(saved[0])
        evidence = []
        for item in conn.execute('SELECT * FROM evidence_blocks WHERE document_id=? ORDER BY physical_page,paragraph_index', (str(document_id),)):
            values = dict(item)
            values['bbox'] = tuple(values.pop(key) for key in ('x0', 'top', 'x1', 'bottom'))
            evidence.append(EvidenceBlock.model_validate(values))
    document_path(settings.db_path, settings.data_dir, document_id)
    return ImportResult(document=Document.model_validate(dict(row)), reused=reused, report=report, evidence=evidence)


def import_pdf(settings: Settings, stream, filename: str, paper_id: UUID | None = None) -> ImportResult:
    digest = hashlib.sha256()
    size = 0
    stream.seek(0)
    header = stream.read(5)
    if header != b'%PDF-':
        raise ImportRejected('文件内容不是 PDF', 415)
    stream.seek(0)
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_BYTES:
            raise ImportRejected('PDF 超过 20 MiB 上限', 413)
        digest.update(chunk)
    stream.seek(0)
    with IMPORT_LOCK:
        with closing(db.connect(settings.db_path)) as conn:
            if paper_id is not None and conn.execute('SELECT id FROM papers WHERE id=?', (str(paper_id),)).fetchone() is None:
                raise ImportRejected('指定文献不存在', 404)
            existing = conn.execute('SELECT * FROM documents WHERE sha256=?', (digest.hexdigest(),)).fetchone()
            saved = conn.execute('SELECT document_id FROM parse_reports WHERE document_id=?', (existing['id'],)).fetchone() if existing else None
        if existing:
            # Reuse the original owning paper/document; never silently reparent it.
            if saved:
                return get_result(settings, UUID(existing['id']), reused=True)
            document = Document.model_validate(dict(existing))
        else:
            created_paper = paper_id is None
            if created_paper:
                paper_id = db.create_paper(settings.db_path, Paper()).id
            try:
                document = store_document(settings.db_path, settings.data_dir, paper_id, stream, filename)
            except BaseException:
                if created_paper:
                    with db.transaction(settings.db_path) as conn:
                        conn.execute('DELETE FROM papers WHERE id=?', (str(paper_id),))
                raise
        report, blocks = inspect_pdf(document_path(settings.db_path, settings.data_dir, document.id), document.id)
        with db.transaction(settings.db_path) as conn:
            for block in blocks:
                conn.execute('''INSERT INTO evidence_blocks
                    (id,document_id,physical_page,printed_page,section,paragraph_index,text,x0,top,x1,bottom,page_width,page_height,rotation)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (str(block.id), str(document.id), block.physical_page, block.printed_page, block.section,
                     block.paragraph_index, block.text, *block.bbox, block.page_width, block.page_height, block.rotation))
            conn.execute('UPDATE documents SET page_count=?,parse_status=?,parse_version=? WHERE id=?',
                         (report.page_count, 'failed' if report.status == 'failed' else 'parsed', PARSER_VERSION, str(document.id)))
            conn.execute('INSERT INTO parse_reports VALUES (?,?)', (str(document.id), report.model_dump_json()))
        return get_result(settings, document.id, reused=existing is not None)
