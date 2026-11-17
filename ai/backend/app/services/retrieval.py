"""Bounded, document-version-scoped text retrieval, shared by later roles."""
from contextlib import closing
from pathlib import Path
from uuid import UUID

from app import db
from app.schemas import EvidenceBlock

MAX_BLOCKS = 40
MAX_CHARS = 16000


def context(db_path: Path, document_id: UUID, anchors: list[UUID]) -> list[EvidenceBlock]:
    """Re-read cited originals first, then same-page neighbours and bounded remaining text."""
    anchors = list(dict.fromkeys(anchors))
    with closing(db.connect(db_path)) as conn:
        rows = conn.execute('SELECT * FROM evidence_blocks WHERE document_id=? ORDER BY physical_page,paragraph_index',
                            (str(document_id),)).fetchall()
    indices = {UUID(row['id']): index for index, row in enumerate(rows)}
    if set(anchors) - indices.keys():
        raise ValueError('引用证据缺失或不属于当前文件版本')
    if len(anchors) > MAX_BLOCKS or sum(len(rows[indices[key]]['text']) for key in anchors) > MAX_CHARS:
        raise ValueError('引用原文超过核查范围上限，请缩短条目范围')
    priority = sorted(indices[key] for key in anchors)
    for index in list(priority):
        for neighbour in (index-1, index+1):
            if 0 <= neighbour < len(rows) and rows[neighbour]['physical_page'] == rows[index]['physical_page']:
                priority.append(neighbour)
    priority.extend(range(len(rows)))
    selected, seen, remaining = [], set(), MAX_CHARS
    for index in priority:
        if index in seen:
            continue
        if len(selected) == MAX_BLOCKS or remaining <= 0:
            break
        seen.add(index)
        values = dict(rows[index])
        values['bbox'] = tuple(values.pop(key) for key in ('x0', 'top', 'x1', 'bottom'))
        values['text'] = values['text'][:remaining]
        remaining -= len(values['text'])
        selected.append(EvidenceBlock.model_validate(values))
    return sorted(selected, key=lambda block: (block.physical_page, block.paragraph_index))


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
