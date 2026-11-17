"""Source-based judgments and a single targeted revision; existence is checked separately."""
import json
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.agents.client import complete
from app.agents.reading import ReadingItem, check_quotes, validate_items
from app.config import PROJECT_ROOT, Settings
from app.schemas import AnalysisItem, EvidenceBlock, EvidenceQuote, Schema

PROMPT_VERSION = 'verification-v1'
REVISION_PROMPT_VERSION = 'revision-v1'


class Verdict(Schema):
    item_id: UUID
    status: Literal['supported', 'partially_supported', 'unsupported', 'not_reported', 'unchecked']
    reason: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[UUID] = Field(default_factory=list)
    quotes: list[EvidenceQuote] = Field(default_factory=list)


class Verdicts(Schema):
    items: list[Verdict] = Field(min_length=1, max_length=14)


class RevisionItem(ReadingItem):
    field: str = Field(min_length=1, max_length=80)


class Revisions(Schema):
    items: list[RevisionItem] = Field(min_length=1, max_length=14)


def request(settings: Settings, items: list[AnalysisItem], evidence: list[EvidenceBlock], *, issues=None):
    revision = issues is not None
    schema = Revisions if revision else Verdicts
    prompt = REVISION_PROMPT_VERSION if revision else PROMPT_VERSION
    system = (PROJECT_ROOT / f'prompts/{prompt}.md').read_text(encoding='utf-8')
    system += '\nJSON Schema:\n' + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    payload = {'claims': [item.model_dump(mode='json') for item in items],
               'paper_content': [{'id': str(block.id), 'physical_page': block.physical_page,
                                  'section': block.section, 'text': block.text} for block in evidence],
               'coverage': '仅本次重新读取的文字；未解析页和未读取图表均不在范围，不能推断全文未报告。'}
    if revision:
        payload['issues'] = [issue.model_dump(mode='json') for issue in issues]
    return complete(settings, system, json.dumps(payload, ensure_ascii=False))


def validate(content: str, items: list[AnalysisItem], evidence: list[EvidenceBlock]) -> list[Verdict]:
    result = Verdicts.model_validate_json(content).items
    expected = {item.id for item in items}
    if len(result) != len(expected) or {item.item_id for item in result} != expected:
        raise ValueError('核查结果必须恰好对应请求条目，不能漏项、重复或改写ID')
    sources = {block.id: block.text for block in evidence}
    for verdict in result:
        if not verdict.reason.strip() or len(verdict.evidence_ids) != len(set(verdict.evidence_ids)):
            raise ValueError('核查理由不能为空，证据ID不能重复')
        check_quotes(verdict.evidence_ids, verdict.quotes, sources,
                     required=verdict.status in {'supported', 'partially_supported', 'unsupported'})
    return result


def validate_revision(content: str, items: list[AnalysisItem], evidence: list[EvidenceBlock]) -> list[AnalysisItem]:
    revised = Revisions.model_validate_json(content).items
    expected = {item.id: item.field for item in items}
    if len(revised) != len(expected) or {item.id: item.field for item in revised} != expected:
        raise ValueError('返工只能修改指定问题条目，须保留ID和字段')
    return validate_items(revised, evidence)


def apply(items: list[AnalysisItem], verdicts: list[Verdict]) -> list[AnalysisItem]:
    by_id = {verdict.item_id: verdict for verdict in verdicts}
    result = []
    for item in items:
        if item.id not in by_id:
            result.append(item)
            continue
        verdict = by_id[item.id]
        values = item.model_dump()
        values.update(verification_status=verdict.status, verification_reason=verdict.reason,
                      evidence_ids=verdict.evidence_ids, quotes=verdict.quotes)
        result.append(AnalysisItem.model_validate(values))
    return result
