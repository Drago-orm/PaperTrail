"""Reading role and deterministic validation; semantic verification is step 06."""
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.config import PROJECT_ROOT, Settings
from app.schemas import AnalysisItem, EvidenceBlock, EvidenceQuote, Schema
from app.agents.client import Completion, complete

PROMPT_VERSION = 'reading-v1'
FIELDS = ('motivation', 'goal', 'method', 'dataset', 'baseline', 'metric', 'ablation', 'result', 'limitation')


class ReadingItem(AnalysisItem):
    field: Literal['motivation', 'goal', 'method', 'dataset', 'baseline', 'metric', 'ablation', 'result', 'limitation']
    content_type: Literal['author_statement', 'system_analysis']
    content: str = Field(min_length=1, max_length=1500)
    verification_status: Literal['unchecked', 'not_reported'] = 'unchecked'


class ReadingOutput(Schema):
    items: list[ReadingItem] = Field(min_length=9, max_length=9)

    @model_validator(mode='after')
    def check_fields(self) -> 'ReadingOutput':
        if {item.field for item in self.items} != set(FIELDS):
            raise ValueError('精读必须恰好覆盖九个字段')
        return self


def validate_output(content: str, evidence: list[EvidenceBlock]) -> list[AnalysisItem]:
    output = ReadingOutput.model_validate_json(content)
    return validate_items(output.items, evidence)


def validate_items(items: list[ReadingItem], evidence: list[EvidenceBlock]) -> list[AnalysisItem]:
    sources = {block.id: block.text for block in evidence}
    checked = []
    for item in items:
        if item.verification_status == 'not_reported':
            if item.content != '未报告' or item.evidence_ids or item.quotes:
                raise ValueError('未报告条目必须明确写未报告且无虚构引用')
        else:
            check_quotes(item.evidence_ids, item.quotes, sources, required=True)
        values = item.model_dump()
        values['verification_reason'] = '仅程序存在性检查；未进行语义核查。未报告仅指本次输入范围。'
        checked.append(AnalysisItem.model_validate(values))
    return checked


def check_quotes(evidence_ids: list[UUID], quotes: list[EvidenceQuote], sources: dict[UUID, str], required: bool) -> None:
    if (required and not evidence_ids) or set(evidence_ids) != {quote.evidence_id for quote in quotes}:
        raise ValueError('分析或核查条目必须保留对应证据与原文片段')
    for quote in quotes:
        if not quote.text.strip() or quote.evidence_id not in sources or quote.text not in sources[quote.evidence_id]:
            raise ValueError('证据ID或原文片段不在本次读取范围')


def replay(file_hash: str, evidence: list[EvidenceBlock]) -> Completion:
    manifest = json.loads((PROJECT_ROOT / 'fixtures/reading-replay.json').read_text(encoding='utf-8'))
    if file_hash != manifest['sha256']:
        raise ValueError('固定回放仅支持 fixtures/reader.pdf，不可用于其他论文')
    source = next((block for block in evidence if manifest['quote'] in block.text), None)
    if source is None:
        raise ValueError('回放引用不在所选范围')
    items = []
    for field in FIELDS:
        item = {'field': field, 'content_type': 'system_analysis', 'content': '未报告',
                'verification_status': 'not_reported', 'evidence_ids': [], 'quotes': []}
        if field == 'method':
            item.update(content=manifest['content'], verification_status='unchecked', evidence_ids=[str(source.id)],
                        quotes=[{'evidence_id': str(source.id), 'text': manifest['quote']}])
        items.append(item)
    return Completion(json.dumps({'items': items}), None, 0)


def read(settings: Settings, evidence: list[EvidenceBlock]) -> Completion:
    system = (PROJECT_ROOT / 'prompts/reading-v1.md').read_text(encoding='utf-8')
    system += '\nJSON Schema:\n' + json.dumps(ReadingOutput.model_json_schema(), ensure_ascii=False)
    context = [{'id': str(block.id), 'physical_page': block.physical_page, 'section': block.section, 'text': block.text} for block in evidence]
    return complete(settings, system, json.dumps({'paper_content': context}, ensure_ascii=False))
