import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.schemas import Note, Paper


SCHEMA_V1 = (
    """CREATE TABLE papers (
        id TEXT NOT NULL PRIMARY KEY, title TEXT NOT NULL, abstract TEXT NOT NULL,
        authors TEXT NOT NULL CHECK(json_valid(authors) AND json_type(authors)='array'),
        keywords TEXT NOT NULL CHECK(json_valid(keywords) AND json_type(keywords)='array'),
        tags TEXT NOT NULL CHECK(json_valid(tags) AND json_type(tags)='array'),
        tasks TEXT NOT NULL CHECK(json_valid(tasks) AND json_type(tasks)='array'),
        methods TEXT NOT NULL CHECK(json_valid(methods) AND json_type(methods)='array'),
        datasets TEXT NOT NULL CHECK(json_valid(datasets) AND json_type(datasets)='array'),
        reading_status TEXT NOT NULL CHECK(reading_status IN ('unread','reading','read')),
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE documents (
        id TEXT NOT NULL PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
        version INTEGER NOT NULL CHECK(version>0),
        sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
        storage_path TEXT NOT NULL CHECK(storage_path='documents/' || id || '.pdf'),
        original_name TEXT NOT NULL,
        page_count INTEGER CHECK(page_count>0),
        parse_status TEXT NOT NULL CHECK(parse_status IN ('pending','parsed','failed')),
        parse_version TEXT, created_at TEXT NOT NULL,
        UNIQUE(paper_id,version), UNIQUE(id,paper_id)
    )""",
    "CREATE INDEX documents_sha256 ON documents(sha256)",
    """CREATE TABLE evidence_blocks (
        id TEXT NOT NULL PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
        physical_page INTEGER NOT NULL CHECK(physical_page>0), printed_page TEXT,
        section TEXT, paragraph_index INTEGER NOT NULL CHECK(paragraph_index>=0),
        text TEXT NOT NULL CHECK(length(text)>0),
        x0 REAL NOT NULL, top REAL NOT NULL, x1 REAL NOT NULL, bottom REAL NOT NULL,
        page_width REAL NOT NULL CHECK(page_width>0),
        page_height REAL NOT NULL CHECK(page_height>0),
        rotation INTEGER NOT NULL CHECK(rotation IN (0,90,180,270)),
        CHECK(0<=x0 AND x0<x1 AND x1<=page_width AND 0<=top AND top<bottom AND bottom<=page_height),
        UNIQUE(id,document_id)
    )""",
    "CREATE INDEX evidence_document_page ON evidence_blocks(document_id,physical_page)",
    """CREATE TABLE notes (
        id TEXT NOT NULL PRIMARY KEY, paper_id TEXT NOT NULL, document_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version>0),
        items_json TEXT NOT NULL CHECK(json_valid(items_json) AND json_type(items_json)='array'),
        writing_items_json TEXT NOT NULL CHECK(json_valid(writing_items_json) AND json_type(writing_items_json)='array'),
        is_user_edited INTEGER NOT NULL CHECK(is_user_edited IN (0,1)),
        parent_note_id TEXT, created_at TEXT NOT NULL,
        CHECK((version=1 AND parent_note_id IS NULL AND is_user_edited=0)
            OR (version>1 AND parent_note_id IS NOT NULL)),
        CHECK(parent_note_id IS NULL OR parent_note_id<>id),
        FOREIGN KEY(document_id,paper_id) REFERENCES documents(id,paper_id),
        FOREIGN KEY(parent_note_id,document_id,paper_id) REFERENCES notes(id,document_id,paper_id),
        UNIQUE(document_id,version), UNIQUE(id,document_id), UNIQUE(id,document_id,paper_id)
    )""",
    """CREATE TABLE ideas (
        id TEXT NOT NULL PRIMARY KEY, question TEXT NOT NULL CHECK(length(question)>0),
        hypothesis TEXT NOT NULL CHECK(length(hypothesis)>0), derivation TEXT NOT NULL,
        difference TEXT NOT NULL, minimum_experiment TEXT NOT NULL, resources TEXT NOT NULL,
        failure_conditions TEXT NOT NULL,
        novelty_checks TEXT NOT NULL CHECK(json_valid(novelty_checks) AND json_type(novelty_checks)='array'),
        status TEXT NOT NULL CHECK(status IN ('draft','confirmed','testing','archived')),
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE idea_sources (
        id TEXT NOT NULL PRIMARY KEY, idea_id TEXT NOT NULL REFERENCES ideas(id),
        paper_id TEXT NOT NULL, document_id TEXT NOT NULL, evidence_id TEXT, note_id TEXT,
        CHECK(evidence_id IS NOT NULL OR note_id IS NOT NULL),
        FOREIGN KEY(document_id,paper_id) REFERENCES documents(id,paper_id),
        FOREIGN KEY(evidence_id,document_id) REFERENCES evidence_blocks(id,document_id),
        FOREIGN KEY(note_id,document_id,paper_id) REFERENCES notes(id,document_id,paper_id)
    )""",
    """CREATE TABLE runs (
        id TEXT NOT NULL PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
        status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed')),
        model_name TEXT, prompt_version TEXT, parse_version TEXT,
        input_scope TEXT NOT NULL CHECK(json_valid(input_scope) AND json_type(input_scope)='object'),
        output TEXT CHECK(output IS NULL OR (json_valid(output) AND json_type(output)='object')),
        elapsed_ms INTEGER CHECK(elapsed_ms>=0), error TEXT, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE run_steps (
        id TEXT NOT NULL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
        role TEXT NOT NULL CHECK(role IN ('reading','writing','verification','idea')),
        attempt INTEGER NOT NULL CHECK(attempt>0),
        status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed')),
        output TEXT CHECK(output IS NULL OR (json_valid(output) AND json_type(output)='object')),
        elapsed_ms INTEGER CHECK(elapsed_ms>=0), error TEXT, UNIQUE(run_id,role,attempt)
    )""",
    """CREATE TABLE merge_events (
        id TEXT NOT NULL PRIMARY KEY, primary_paper_id TEXT NOT NULL REFERENCES papers(id),
        secondary_paper_id TEXT NOT NULL REFERENCES papers(id),
        before_snapshot TEXT NOT NULL CHECK(json_valid(before_snapshot) AND json_type(before_snapshot)='object'),
        changes TEXT NOT NULL CHECK(json_valid(changes) AND json_type(changes)='object'),
        status TEXT NOT NULL CHECK(status IN ('applied','reverted')),
        created_at TEXT NOT NULL, reverted_at TEXT,
        CHECK(primary_paper_id<>secondary_paper_id),
        CHECK((status='applied' AND reverted_at IS NULL) OR (status='reverted' AND reverted_at IS NOT NULL))
    )""",
)
MIGRATIONS = (SCHEMA_V1,)


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with transaction(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if not 0 <= version <= len(MIGRATIONS):
            raise RuntimeError(f"数据库版本 {version} 不受当前程序支持（0..{len(MIGRATIONS)}）")
        for index in range(version, len(MIGRATIONS)):
            for statement in MIGRATIONS[index]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={index + 1}")


def create_paper(db_path: Path, paper: Paper) -> Paper:
    paper = Paper.model_validate(paper.model_dump())
    with transaction(db_path) as connection:
        connection.execute(
            """INSERT INTO papers
               (id,title,abstract,authors,keywords,tags,tasks,methods,datasets,reading_status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(paper.id), paper.title, paper.abstract,
                *(json.dumps(getattr(paper, name), ensure_ascii=False) for name in (
                    "authors", "keywords", "tags", "tasks", "methods", "datasets"
                )),
                paper.reading_status, paper.created_at.isoformat(),
            ),
        )
    return paper


def create_note(db_path: Path, note: Note) -> Note:
    note = Note.model_validate(note.model_dump())
    with transaction(db_path) as connection:
        if note.parent_note_id is not None:
            parent = connection.execute(
                "SELECT version FROM notes WHERE id=? AND document_id=? AND paper_id=?",
                (str(note.parent_note_id), str(note.document_id), str(note.paper_id)),
            ).fetchone()
            if parent is None or parent["version"] + 1 != note.version:
                raise ValueError("父笔记快照必须属于同一文档且版本连续")
        evidence_ids = {
            str(evidence_id) for item in note.items + note.writing_items
            for evidence_id in item.evidence_ids
        }
        for evidence_id in evidence_ids:
            row = connection.execute(
                "SELECT document_id FROM evidence_blocks WHERE id=?", (evidence_id,)
            ).fetchone()
            if row is None or row["document_id"] != str(note.document_id):
                raise ValueError("笔记证据必须存在并属于当前文件版本")
        connection.execute(
            """INSERT INTO notes
               (id,paper_id,document_id,version,items_json,writing_items_json,is_user_edited,parent_note_id,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                str(note.id), str(note.paper_id), str(note.document_id), note.version,
                json.dumps([item.model_dump(mode="json") for item in note.items], ensure_ascii=False),
                json.dumps([item.model_dump(mode="json") for item in note.writing_items], ensure_ascii=False),
                int(note.is_user_edited), str(note.parent_note_id) if note.parent_note_id else None,
                note.created_at.isoformat(),
            ),
        )
    return note
