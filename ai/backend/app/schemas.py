from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Paper(Schema):
    id: UUID = Field(default_factory=uuid4)
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    reading_status: Literal["unread", "reading", "read"] = "unread"
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Document(Schema):
    id: UUID = Field(default_factory=uuid4)
    paper_id: UUID
    version: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_path: str
    original_name: str
    page_count: int | None = Field(default=None, gt=0)
    parse_status: Literal["pending", "parsed", "failed"] = "pending"
    parse_version: str | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def check_storage_path(self) -> "Document":
        if self.storage_path != f"documents/{self.id}.pdf":
            raise ValueError("storage_path 必须由文档 ID 生成")
        return self


class EvidenceBlock(Schema):
    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    physical_page: int = Field(gt=0)
    printed_page: str | None = None
    section: str | None = None
    paragraph_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    bbox: tuple[float, float, float, float]
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = 0

    @model_validator(mode="after")
    def check_bbox(self) -> "EvidenceBlock":
        x0, top, x1, bottom = self.bbox
        if not (0 <= x0 < x1 <= self.page_width and 0 <= top < bottom <= self.page_height):
            raise ValueError("证据坐标必须位于当前物理页面内，采用左上角原点的 PDF 点坐标")
        return self


class AnalysisItem(Schema):
    id: UUID = Field(default_factory=uuid4)
    field: str = Field(min_length=1)
    content_type: Literal["author_statement", "system_analysis", "research_idea"]
    content: str = Field(min_length=1)
    evidence_ids: list[UUID] = Field(default_factory=list)
    verification_status: Literal[
        "unchecked", "supported", "partially_supported", "unsupported", "not_reported"
    ] = "unchecked"
    verification_reason: str = ""

    @model_validator(mode="after")
    def check_verified_source(self) -> "AnalysisItem":
        if self.verification_status in {"supported", "partially_supported"} and not self.evidence_ids:
            raise ValueError("支持或部分支持的核查结论必须保留证据 ID")
        return self

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("证据 ID 不能重复")
        return value


class Note(Schema):
    id: UUID = Field(default_factory=uuid4)
    paper_id: UUID
    document_id: UUID
    version: int = Field(default=1, gt=0)
    items: list[AnalysisItem] = Field(default_factory=list)
    writing_items: list[AnalysisItem] = Field(default_factory=list)
    is_user_edited: bool = False
    parent_note_id: UUID | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def check_snapshot(self) -> "Note":
        ids = [item.id for item in self.items + self.writing_items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一笔记快照的分析条目 ID 不能重复")
        if (self.version == 1) != (self.parent_note_id is None):
            raise ValueError("笔记新版本必须保留父快照 ID，首版不能指定父快照")
        if self.is_user_edited and self.version == 1:
            raise ValueError("人工修订必须形成新版本")
        if self.parent_note_id == self.id:
            raise ValueError("笔记快照不能引用自己作为父快照")
        return self


class Idea(Schema):
    id: UUID = Field(default_factory=uuid4)
    question: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    derivation: str = ""
    difference: str = ""
    minimum_experiment: str = ""
    resources: str = ""
    failure_conditions: str = ""
    novelty_checks: list[str] = Field(default_factory=list)
    status: Literal["draft", "confirmed", "testing", "archived"] = "draft"
    created_at: AwareDatetime = Field(default_factory=utc_now)


class IdeaSource(Schema):
    id: UUID = Field(default_factory=uuid4)
    idea_id: UUID
    paper_id: UUID
    document_id: UUID
    evidence_id: UUID | None = None
    note_id: UUID | None = None

    @model_validator(mode="after")
    def check_source(self) -> "IdeaSource":
        if self.evidence_id is None and self.note_id is None:
            raise ValueError("Idea 来源必须关联具体证据或笔记快照")
        return self


RunStatus = Literal["pending", "running", "succeeded", "failed"]


class Run(Schema):
    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    status: RunStatus = "pending"
    model_name: str | None = None
    prompt_version: str | None = None
    parse_version: str | None = None
    input_scope: dict[str, JsonValue] = Field(default_factory=dict)
    output: dict[str, JsonValue] | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    error: str | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)


class RunStep(Schema):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    role: Literal["reading", "writing", "verification", "idea"]
    attempt: int = Field(default=1, gt=0)
    status: RunStatus = "pending"
    output: dict[str, JsonValue] | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    error: str | None = None


class MergeEvent(Schema):
    id: UUID = Field(default_factory=uuid4)
    primary_paper_id: UUID
    secondary_paper_id: UUID
    before_snapshot: dict[str, JsonValue]
    changes: dict[str, JsonValue]
    status: Literal["applied", "reverted"] = "applied"
    created_at: AwareDatetime = Field(default_factory=utc_now)
    reverted_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def check_papers(self) -> "MergeEvent":
        if self.primary_paper_id == self.secondary_paper_id:
            raise ValueError("不能合并同一个文献条目")
        if (self.status == "reverted") != (self.reverted_at is not None):
            raise ValueError("撤销状态必须包含真实撤销时间")
        return self
