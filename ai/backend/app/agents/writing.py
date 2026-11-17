"""Five source-bound writing strategies, not imitation paragraphs."""
import json
from typing import Literal

from pydantic import Field, model_validator

from app.agents.client import complete
from app.agents.reading import ReadingItem, validate_items
from app.config import PROJECT_ROOT, Settings
from app.schemas import AnalysisItem, EvidenceBlock, Schema

PROMPT_VERSION = 'writing-v1'
FIELDS = ('problem_introduction', 'contribution_organization', 'method_explanation', 'experiment_arrangement', 'limitation_discussion')


class WritingItem(ReadingItem):
    field: Literal['problem_introduction', 'contribution_organization', 'method_explanation', 'experiment_arrangement', 'limitation_discussion']


class WritingOutput(Schema):
    items: list[WritingItem] = Field(min_length=5, max_length=5)

    @model_validator(mode='after')
    def check_fields(self):
        if {item.field for item in self.items} != set(FIELDS):
            raise ValueError('写作分析必须恰好覆盖五个字段')
        return self


def read(settings: Settings, evidence: list[EvidenceBlock], items: list[AnalysisItem], conditions: dict):
    system = (PROJECT_ROOT/'prompts/writing-v1.md').read_text(encoding='utf-8')
    system += '\nJSON Schema:\n' + json.dumps(WritingOutput.model_json_schema(), ensure_ascii=False)
    payload = {'paper_content': [{'id': str(block.id), 'physical_page': block.physical_page,
                                 'section': block.section, 'text': block.text} for block in evidence],
               'reading_items': [item.model_dump(mode='json') for item in items], 'conditions': conditions}
    return complete(settings, system, json.dumps(payload, ensure_ascii=False))


def validate_output(content: str, evidence: list[EvidenceBlock]) -> list[AnalysisItem]:
    return validate_items(WritingOutput.model_validate_json(content).items, evidence)
