"""Research proposals refer to immutable supported sources; experimental plans are not facts."""
import json
from uuid import UUID

from pydantic import Field

from app.agents.client import complete
from app.config import PROJECT_ROOT, Settings
from app.schemas import MinimumExperiment, Schema

PROMPT_VERSION = 'ideas-v1'


class SourceChoice(Schema):
    note_id: UUID
    item_id: UUID


class IdeaDraft(Schema):
    question: str = Field(min_length=1, max_length=800)
    hypothesis: str = Field(min_length=1, max_length=1200)
    derivation: str = Field(min_length=1, max_length=1500)
    difference: str = Field(min_length=1, max_length=1000)
    resources: str = Field(min_length=1, max_length=1000)
    failure_conditions: str = Field(min_length=1, max_length=1000)
    novelty_checks: list[str] = Field(min_length=1, max_length=8)
    experiment: MinimumExperiment
    sources: list[SourceChoice] = Field(min_length=1, max_length=6)


def generate(settings: Settings, facts: list[dict], evidence: list[dict], conditions: dict):
    system = (PROJECT_ROOT/f'prompts/{PROMPT_VERSION}.md').read_text(encoding='utf-8')
    system += '\nJSON Schema:\n' + json.dumps(IdeaDraft.model_json_schema(), ensure_ascii=False)
    return complete(settings, system, json.dumps({'verified_sources': facts, 'paper_content': evidence,
                                                 'conditions': conditions}, ensure_ascii=False))
