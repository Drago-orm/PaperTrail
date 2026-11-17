import hashlib
from io import BytesIO
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from app import db
from app.schemas import Document, Paper
from app.services import storage


# These bytes exercise storage only; they are not a PDF parsing fixture.
SAMPLE_BYTES = b"Storage sample bytes, not a parsed PDF.\n"


@pytest.fixture
def library(tmp_path: Path) -> tuple[Path, Path, Paper]:
    db_path = tmp_path / "data" / "papertrail.sqlite3"
    data_dir = db_path.parent
    db.initialize(db_path)
    paper = db.create_paper(db_path, Paper(title="Storage boundary checks"))
    return db_path, data_dir, paper


def persisted_documents(db_path: Path) -> list[Document]:
    connection = db.connect(db_path)
    try:
        rows = connection.execute("SELECT * FROM documents ORDER BY version").fetchall()
        return [Document.model_validate(dict(row)) for row in rows]
    finally:
        connection.close()


def test_stream_hash_versions_and_reopen(library: tuple[Path, Path, Paper]) -> None:
    db_path, data_dir, paper = library

    class BoundedStream(BytesIO):
        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 1024 * 1024
            return super().read(size)

    payload = SAMPLE_BYTES * 40_000
    stream = BoundedStream(payload)
    first = storage.store_document(db_path, data_dir, paper.id, stream, "first.pdf")
    second = storage.store_document(
        db_path, data_dir, str(paper.id), BytesIO(payload), "second.pdf"
    )
    assert not stream.closed
    assert first.id != second.id
    assert [first.version, second.version] == [1, 2]
    assert first.sha256 == second.sha256 == hashlib.sha256(payload).hexdigest()
    assert first.parse_status == "pending"
    assert first.page_count is None and first.parse_version is None
    db.initialize(db_path)
    assert persisted_documents(db_path) == [first, second]
    assert storage.document_path(db_path, data_dir, first.id).read_bytes() == payload
    assert storage.document_path(db_path, data_dir, str(second.id)).read_bytes() == payload

    other_paper = db.create_paper(db_path, Paper(title="Independent versions"))
    other = storage.store_document(
        db_path, data_dir, other_paper.id, BytesIO(SAMPLE_BYTES), "other.pdf"
    )
    assert other.version == 1


@pytest.mark.parametrize("name", ["../../outside.pdf", r"C:\outside.pdf", "evil\x00.pdf"])
def test_original_name_is_metadata_only(
    library: tuple[Path, Path, Paper], name: str
) -> None:
    db_path, data_dir, paper = library
    document = storage.store_document(
        db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), name
    )
    assert document.original_name == name
    assert document.storage_path == f"documents/{document.id}.pdf"
    assert storage.document_path(db_path, data_dir, document.id) == (
        data_dir / "documents" / f"{document.id}.pdf"
    ).resolve()
    assert persisted_documents(db_path)[0].original_name == name


@pytest.mark.parametrize("bad_id", ["../../escape", r"C:\outside.pdf", "not-a-uuid", ""])
def test_invalid_ids_cannot_choose_paths(
    library: tuple[Path, Path, Paper], bad_id: str
) -> None:
    db_path, data_dir, _ = library
    with pytest.raises(ValueError):
        storage.document_path(db_path, data_dir, bad_id)
    with pytest.raises(ValueError):
        storage.store_document(db_path, data_dir, bad_id, BytesIO(SAMPLE_BYTES), "x.pdf")
    assert not (data_dir / "documents").exists()
    assert persisted_documents(db_path) == []


def test_unknown_paper_rolls_back_file_and_metadata(
    library: tuple[Path, Path, Paper],
) -> None:
    db_path, data_dir, _ = library
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        storage.store_document(db_path, data_dir, uuid4(), BytesIO(SAMPLE_BYTES), "x.pdf")
    assert list((data_dir / "documents").iterdir()) == []
    assert persisted_documents(db_path) == []


def test_failed_stream_preserves_existing_file_and_version(
    library: tuple[Path, Path, Paper],
) -> None:
    db_path, data_dir, paper = library
    first = storage.store_document(
        db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), "existing.pdf"
    )

    class FailedStream(BytesIO):
        def read(self, size: int = -1) -> bytes:
            if self.tell():
                raise OSError("source stream failed")
            return super().read(size)

    with pytest.raises(OSError, match="source stream failed"):
        storage.store_document(
            db_path, data_dir, paper.id, FailedStream(SAMPLE_BYTES), "failed.pdf"
        )
    assert persisted_documents(db_path) == [first]
    assert list((data_dir / "documents").iterdir()) == [
        storage.document_path(db_path, data_dir, first.id)
    ]
    assert storage.document_path(db_path, data_dir, first.id).read_bytes() == SAMPLE_BYTES
    second = storage.store_document(
        db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), "next.pdf"
    )
    assert second.version == 2


@pytest.mark.parametrize("fail_on_commit", [False, True])
def test_database_failure_removes_created_file(
    library: tuple[Path, Path, Paper], fail_on_commit: bool
) -> None:
    db_path, data_dir, paper = library
    with db.transaction(db_path) as connection:
        if fail_on_commit:
            # The deferred FK fails at COMMIT, after the document INSERT succeeds.
            connection.execute(
                """CREATE TABLE commit_failure (
                    paper_id TEXT REFERENCES papers(id) DEFERRABLE INITIALLY DEFERRED
                )"""
            )
            connection.execute(
                """CREATE TRIGGER reject_document AFTER INSERT ON documents BEGIN
                    INSERT INTO commit_failure (paper_id) VALUES ('missing-paper');
                END"""
            )
        else:
            connection.execute(
                """CREATE TRIGGER reject_document BEFORE INSERT ON documents BEGIN
                    SELECT RAISE(ABORT, 'document rejected');
                END"""
            )
    with pytest.raises(sqlite3.IntegrityError):
        storage.store_document(db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), "x.pdf")
    assert list((data_dir / "documents").iterdir()) == []
    assert persisted_documents(db_path) == []


def test_generated_id_collision_does_not_delete_existing_file(
    library: tuple[Path, Path, Paper], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, data_dir, paper = library
    first = storage.store_document(
        db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), "existing.pdf"
    )
    monkeypatch.setattr(storage, "uuid4", lambda: first.id)
    with pytest.raises(FileExistsError):
        storage.store_document(
            db_path, data_dir, paper.id, BytesIO(b"replacement"), "collision.pdf"
        )
    assert persisted_documents(db_path) == [first]
    assert storage.document_path(db_path, data_dir, first.id).read_bytes() == SAMPLE_BYTES


@pytest.mark.parametrize(
    "bad_path",
    ["../outside.pdf", "/outside.pdf", r"C:\outside.pdf", f"documents/{uuid4()}.pdf"],
)
def test_read_rejects_tampered_database_path(
    library: tuple[Path, Path, Paper], bad_path: str
) -> None:
    db_path, data_dir, paper = library
    document = storage.store_document(
        db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), "x.pdf"
    )
    with db.transaction(db_path) as connection:
        # Simulate offline database corruption, beyond normal SQL CHECK protection.
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE documents SET storage_path = ? WHERE id = ?", (bad_path, str(document.id))
        )
    with pytest.raises(ValueError, match="does not match its ID"):
        storage.document_path(db_path, data_dir, document.id)


def test_missing_document_and_missing_file_fail_explicitly(
    library: tuple[Path, Path, Paper],
) -> None:
    db_path, data_dir, paper = library
    with pytest.raises(LookupError, match="was not found"):
        storage.document_path(db_path, data_dir, uuid4())
    document = storage.store_document(
        db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), "x.pdf"
    )
    storage.document_path(db_path, data_dir, document.id).unlink()
    with pytest.raises(FileNotFoundError, match="Stored file"):
        storage.document_path(db_path, data_dir, document.id)


def test_directory_link_cannot_escape_data_root(
    library: tuple[Path, Path, Paper], tmp_path: Path
) -> None:
    db_path, data_dir, paper = library
    document = storage.store_document(
        db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), "x.pdf"
    )
    storage.document_path(db_path, data_dir, document.id).unlink()
    directory = data_dir / "documents"
    directory.rmdir()
    outside = tmp_path / "outside files"
    outside.mkdir()
    marker = outside / f"{document.id}.pdf"
    marker.write_bytes(b"outside data must remain untouched")
    if os.name == "nt":
        # CPython's native helper supports long Windows temporary paths.
        import _winapi

        _winapi.CreateJunction(str(outside), str(directory))
    else:
        directory.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="escapes the data directory"):
            storage.document_path(db_path, data_dir, document.id)
        with pytest.raises(ValueError, match="escapes the data directory"):
            storage.store_document(
                db_path, data_dir, paper.id, BytesIO(SAMPLE_BYTES), "new.pdf"
            )
        assert list(outside.iterdir()) == [marker]
        assert marker.read_bytes() == b"outside data must remain untouched"
        assert persisted_documents(db_path) == [document]
    finally:
        if os.name == "nt":
            directory.rmdir()
        else:
            directory.unlink()
