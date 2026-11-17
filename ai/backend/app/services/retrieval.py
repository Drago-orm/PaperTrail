"""Bounded, document-version-scoped text retrieval, shared by later roles."""
from contextlib import closing
from pathlib import Path
from uuid import UUID

from app import db
from app.schemas import EvidenceBlock

MAX_BLOCKS = 40
MAX_CHARS = 16000


def retrieve(db_path: Path, document_id: UUID, section: str = '', query: str = '') -> list[EvidenceBlock]:
    selected = []
    remaining = MAX_CHARS
    with closing(db.connect(db_path)) as conn:
        rows = conn.execute('SELECT * FROM evidence_blocks WHERE document_id=? ORDER BY physical_page,paragraph_index', (str(document_id),))
        for row in rows:
            if section and section.casefold() not in (row['section'] or '').casefold():
                continue
            if query and query.casefold() not in row['text'].casefold():
                continue
            if len(selected) == MAX_BLOCKS or remaining <= 0:
                break
            values = dict(row)
            values['bbox'] = tuple(values.pop(key) for key in ('x0', 'top', 'x1', 'bottom'))
            values['text'] = values['text'][:remaining]
            remaining -= len(values['text'])
            selected.append(EvidenceBlock.model_validate(values))
    return selected
