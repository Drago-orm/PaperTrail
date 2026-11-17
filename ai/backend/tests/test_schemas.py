from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas import AnalysisItem, EvidenceBlock, Note


PAPER_ID = UUID("c83ddc92-fefe-4ef1-9eed-0393e1764ace")
DOCUMENT_ID = UUID("9e49adfa-4282-41e8-a38e-f62497c85dd4")
EVIDENCE_ID = UUID("e65cd6e9-c81e-4702-9f51-17dba312cbed")
ITEM_ID = UUID("9051c95e-ee04-4ee3-bb67-e069c740c14c")


def evidence_data() -> dict[str, object]:
    return {
        "id": EVIDENCE_ID,
        "document_id": DOCUMENT_ID,
        "physical_page": 2,
        "printed_page": "1",
        "paragraph_index": 0,
        "text": "Self-authored evidence for validation tests.",
        "bbox": (0.0, 0.0, 612.0, 792.0),
        "page_width": 612.0,
        "page_height": 792.0,
        "rotation": 90,
    }


def analysis_data() -> dict[str, object]:
    return {
        "id": ITEM_ID,
        "field": "method",
        "content_type": "author_statement",
        "content": "A statement linked to its source document.",
        "evidence_ids": [EVIDENCE_ID],
        "verification_status": "supported",
        "verification_reason": "The original passage states this method.",
    }


def test_evidence_keeps_file_and_physical_page_when_json_round_trips():
    evidence = EvidenceBlock.model_validate(evidence_data())

    restored = EvidenceBlock.model_validate_json(evidence.model_dump_json())

    assert restored == evidence
    assert restored.document_id == DOCUMENT_ID
    assert restored.physical_page == 2
    assert restored.printed_page == "1"
    assert restored.bbox == (0.0, 0.0, 612.0, 792.0)
    assert restored.rotation == 90


@pytest.mark.parametrize(
    "changes",
    [
        {"physical_page": 0},
        {"paragraph_index": -1},
        {"bbox": (200.0, 10.0, 100.0, 40.0)},
        {"bbox": (0.0, 0.0, 613.0, 792.0)},
        {"bbox": (0.0, 0.0, float("nan"), 10.0)},
        {"rotation": 45},
    ],
)
def test_evidence_rejects_invalid_physical_locations(changes: dict[str, object]):
    with pytest.raises(ValidationError):
        EvidenceBlock.model_validate(evidence_data() | changes)


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"content_type": "guaranteed_fact"}, "content_type"),
        ({"verification_status": "certain"}, "verification_status"),
        ({"evidence_ids": ["invented-evidence-id"]}, "evidence_ids"),
        ({"evidence_ids": [EVIDENCE_ID, EVIDENCE_ID]}, "evidence_ids"),
    ],
)
def test_analysis_rejects_invalid_labels_and_evidence_ids(
    changes: dict[str, object], field: str
):
    with pytest.raises(ValidationError) as error:
        AnalysisItem.model_validate(analysis_data() | changes)

    assert error.value.errors()[0]["loc"][0] == field


@pytest.mark.parametrize("status", ["supported", "partially_supported"])
def test_verified_support_requires_an_evidence_source(status: str):
    with pytest.raises(ValidationError):
        AnalysisItem.model_validate(
            analysis_data() | {"verification_status": status, "evidence_ids": []}
        )


@pytest.mark.parametrize("status", ["unchecked", "not_reported"])
def test_unverified_or_unreported_content_can_keep_empty_evidence(status: str):
    item = AnalysisItem.model_validate(
        analysis_data() | {"verification_status": status, "evidence_ids": []}
    )

    assert item.evidence_ids == []
    assert item.verification_status == status


def test_human_revision_json_preserves_source_and_stable_item_id():
    original = Note(
        paper_id=PAPER_ID,
        document_id=DOCUMENT_ID,
        items=[AnalysisItem.model_validate(analysis_data())],
    )
    original_json = original.model_dump_json()
    revised = Note(
        paper_id=PAPER_ID,
        document_id=DOCUMENT_ID,
        version=2,
        parent_note_id=original.id,
        is_user_edited=True,
        items=[
            AnalysisItem.model_validate(
                analysis_data() | {"content": "A user's corrected statement."}
            )
        ],
    )

    restored = Note.model_validate_json(revised.model_dump_json())

    assert original.model_dump_json() == original_json
    assert restored == revised
    assert restored.id != original.id
    assert restored.parent_note_id == original.id
    assert restored.version == 2
    assert restored.is_user_edited is True
    assert restored.document_id == original.document_id
    assert restored.items[0].id == original.items[0].id == ITEM_ID
    assert restored.items[0].evidence_ids == [EVIDENCE_ID]
    assert restored.items[0].content == "A user's corrected statement."
    assert restored.items[0].verification_status == "supported"


@pytest.mark.parametrize(
    "changes",
    [
        {"is_user_edited": True},
        {"version": 2},
        {"parent_note_id": UUID("c37b44da-d456-4879-be74-ae5e0bda6b23")},
        {
            "id": UUID("c37b44da-d456-4879-be74-ae5e0bda6b23"),
            "version": 2,
            "parent_note_id": UUID("c37b44da-d456-4879-be74-ae5e0bda6b23"),
        },
        {
            "items": [analysis_data()],
            "writing_items": [analysis_data()],
        },
    ],
)
def test_note_rejects_lost_history_and_duplicate_item_ids(changes: dict[str, object]):
    with pytest.raises(ValidationError):
        Note.model_validate(
            {"paper_id": PAPER_ID, "document_id": DOCUMENT_ID} | changes
        )
