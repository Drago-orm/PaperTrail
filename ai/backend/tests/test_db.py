import json
import sqlite3
from uuid import uuid4

import pytest

from app import db
from app.schemas import AnalysisItem, Document, EvidenceBlock, Note, Paper


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "data" / "papertrail.sqlite3"
    db.initialize(path)
    return path


def add_document(path, paper, version=1):
    document_id = uuid4()
    document = Document(
        id=document_id, paper_id=paper.id, version=version,
        sha256="0" * 64, storage_path=f"documents/{document_id}.pdf", original_name="自制样例.pdf",
        page_count=2,
    )
    with db.transaction(path) as connection:
        connection.execute(
            """INSERT INTO documents
               (id,paper_id,version,sha256,storage_path,original_name,page_count,parse_status,parse_version,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (str(document.id), str(paper.id), version, document.sha256, document.storage_path,
             document.original_name, document.page_count, document.parse_status,
             document.parse_version, document.created_at.isoformat()),
        )
    return document


def add_evidence(path, document):
    evidence = EvidenceBlock(
        document_id=document.id, physical_page=1, paragraph_index=0,
        text="Self-authored test evidence.", bbox=(0, 0, 100, 50),
        page_width=200, page_height=300,
    )
    with db.transaction(path) as connection:
        connection.execute(
            """INSERT INTO evidence_blocks
               (id,document_id,physical_page,paragraph_index,text,x0,top,x1,bottom,page_width,page_height,rotation)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(evidence.id), str(document.id), evidence.physical_page, evidence.paragraph_index,
             evidence.text, *evidence.bbox, evidence.page_width, evidence.page_height, evidence.rotation),
        )
    return evidence


def test_initialization_is_idempotent_and_data_survives_reopen(db_path):
    paper = db.create_paper(db_path, Paper(title="O'Reilly 参数化写入", authors=["测试作者"]))
    db.initialize(db_path)
    connection = db.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        row = connection.execute("SELECT * FROM papers WHERE id=?", (str(paper.id),)).fetchone()
        assert row["title"] == paper.title
        assert json.loads(row["authors"]) == paper.authors
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {
            "papers", "documents", "evidence_blocks", "notes", "ideas", "idea_sources",
            "runs", "run_steps", "merge_events", "parse_reports",
        }
    finally:
        connection.close()


def test_transaction_rolls_back_and_closes_connection(db_path):
    with pytest.raises(RuntimeError, match="abort"):
        with db.transaction(db_path) as connection:
            connection.execute("DELETE FROM papers")
            connection.execute(
                """INSERT INTO papers (id,title,abstract,authors,keywords,tags,tasks,methods,datasets,reading_status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid4()), "must rollback", "", "[]", "[]", "[]", "[]", "[]", "[]", "unread", "now"),
            )
            raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    reopened = db.connect(db_path)
    try:
        assert reopened.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
    finally:
        reopened.close()


def test_v3_notes_migrate_without_inventing_origin(tmp_path, monkeypatch):
    path = tmp_path / 'v3.sqlite3'
    with monkeypatch.context() as old:
        old.setattr(db, 'MIGRATIONS', db.MIGRATIONS[:3])
        db.initialize(path)
    paper = db.create_paper(path, Paper(title='Existing notes'))
    document = add_document(path, paper)
    note_id = str(uuid4())
    with db.transaction(path) as connection:
        connection.execute('''INSERT INTO notes
            (id,paper_id,document_id,version,items_json,writing_items_json,is_user_edited,created_at)
            VALUES (?,?,?,1,'[]','[]',0,'2026-11-17T00:00:00Z')''',
            (note_id, str(paper.id), str(document.id)))
    db.initialize(path)
    with db.transaction(path) as connection:
        row = connection.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
        assert row['source_mode'] == 'unknown' and row['version'] == 1
        assert row['verification_run_id'] is None


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    path = tmp_path / "failed.sqlite3"
    monkeypatch.setattr(db, "MIGRATIONS", (db.SCHEMA_V1 + ("THIS IS NOT SQL",),))
    with pytest.raises(sqlite3.OperationalError):
        db.initialize(path)
    connection = db.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize("version", [-1, 99])
def test_unsupported_version_is_not_changed(db_path, version):
    with db.transaction(db_path) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    with pytest.raises(RuntimeError, match=str(version)):
        db.initialize(db_path)
    connection = db.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version
    finally:
        connection.close()


def test_document_foreign_key_rejects_unknown_paper(db_path):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        add_document(db_path, Paper())


def test_notes_validate_evidence_and_preserve_manual_snapshot(db_path):
    paper = db.create_paper(db_path, Paper())
    document = add_document(db_path, paper)
    evidence = add_evidence(db_path, document)
    item = AnalysisItem(
        field="method", content_type="author_statement", content="原始测试笔记",
        evidence_ids=[evidence.id],
    )
    original = db.create_note(db_path, Note(paper_id=paper.id, document_id=document.id, items=[item]))
    edited_item = item.model_copy(update={"content": "人工修订测试笔记"})
    edited = db.create_note(db_path, Note(
        paper_id=paper.id, document_id=document.id, version=2, items=[edited_item],
        is_user_edited=True, parent_note_id=original.id,
    ))
    connection = db.connect(db_path)
    try:
        rows = connection.execute("SELECT * FROM notes ORDER BY version").fetchall()
        assert len(rows) == 2
        assert json.loads(rows[0]["items_json"])[0]["content"] == item.content
        assert json.loads(rows[1]["items_json"])[0]["id"] == str(item.id)
        assert json.loads(rows[1]["items_json"])[0]["content"] == edited_item.content
        assert rows[1]["parent_note_id"] == str(original.id)
        assert rows[1]["id"] == str(edited.id)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE notes SET items_json=? WHERE id=?", ("not-json", str(original.id)))
    finally:
        connection.close()
    other_document = add_document(db_path, paper, version=2)
    with pytest.raises(ValueError, match="当前文件版本"):
        db.create_note(db_path, Note(paper_id=paper.id, document_id=other_document.id, items=[item]))
    unknown_item = item.model_copy(update={"evidence_ids": [uuid4()]})
    with pytest.raises(ValueError, match="必须存在"):
        db.create_note(db_path, Note(paper_id=paper.id, document_id=document.id, items=[unknown_item]))


def test_idea_source_rejects_evidence_from_other_document_version(db_path):
    paper = db.create_paper(db_path, Paper())
    first = add_document(db_path, paper)
    second = add_document(db_path, paper, version=2)
    evidence = add_evidence(db_path, first)
    idea_id = uuid4()
    with db.transaction(db_path) as connection:
        connection.execute(
            "INSERT INTO ideas VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(idea_id), "test question", "test hypothesis", "", "", "", "", "", "[]", "draft", "now"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with db.transaction(db_path) as connection:
            connection.execute(
                "INSERT INTO idea_sources VALUES (?,?,?,?,?,?)",
                (str(uuid4()), str(idea_id), str(paper.id), str(second.id), str(evidence.id), None),
            )
