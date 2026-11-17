"""Store document bytes under server-generated IDs; PDF validation comes later."""

import hashlib
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from app import db
from app.schemas import Document


def _document_location(data_dir: Path, document_id: UUID, storage_path: str) -> Path:
    expected = f"documents/{document_id}.pdf"
    if storage_path != expected:
        raise ValueError("Document storage path does not match its ID")
    root = data_dir.resolve()
    target = (root / storage_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Document storage path escapes the data directory")
    return target


def store_document(
    db_path: Path,
    data_dir: Path,
    paper_id: UUID | str,
    source: BinaryIO,
    original_name: str,
) -> Document:
    """Save a stream and its metadata, rolling back both on failure.

    This service stores bytes only. The upload service must validate PDF format
    and size before calling it. The caller retains ownership of the stream.
    """
    paper_id = UUID(str(paper_id))
    document_id = uuid4()
    storage_path = f"documents/{document_id}.pdf"
    target = _document_location(data_dir, document_id, storage_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _document_location(data_dir, document_id, storage_path)
    created_file = False
    try:
        with db.transaction(db_path) as connection:
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM documents WHERE paper_id = ?",
                (str(paper_id),),
            ).fetchone()[0]
            digest = hashlib.sha256()
            with target.open("xb") as output:
                created_file = True
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            document = Document(
                id=document_id,
                paper_id=paper_id,
                version=version,
                sha256=digest.hexdigest(),
                storage_path=storage_path,
                original_name=original_name,
            )
            connection.execute(
                """INSERT INTO documents (
                    id, paper_id, version, sha256, storage_path, original_name,
                    page_count, parse_status, parse_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(document.id),
                    str(document.paper_id),
                    document.version,
                    document.sha256,
                    document.storage_path,
                    document.original_name,
                    document.page_count,
                    document.parse_status,
                    document.parse_version,
                    document.created_at.isoformat(),
                ),
            )
        return document
    except BaseException:
        if created_file:
            target.unlink(missing_ok=True)
        raise


def document_path(db_path: Path, data_dir: Path, document_id: UUID | str) -> Path:
    """Resolve an existing document through its database ID, never a user path."""
    document_id = UUID(str(document_id))
    connection = db.connect(db_path)
    try:
        row = connection.execute(
            "SELECT storage_path FROM documents WHERE id = ?", (str(document_id),)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise LookupError(f"Document {document_id} was not found")
    target = _document_location(data_dir, document_id, row["storage_path"])
    if not target.is_file():
        raise FileNotFoundError(f"Stored file for document {document_id} was not found")
    return target
