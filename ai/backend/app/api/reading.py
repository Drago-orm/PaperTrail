from contextlib import closing
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app import db
from app.agents.client import ModelError
from app.config import Settings
from app.schemas import Note, Run, Schema
from app.services.reading_runs import get_run, list_notes, run_from_row, save_manual


class ReadingRequest(Schema):
    mode: Literal['live', 'replay'] = 'live'
    section: str = Field(default='', max_length=200)
    query: str = Field(default='', max_length=200)


class ManualRequest(Schema):
    base_note_id: UUID
    changes: dict[UUID, str]


def reading_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post('/api/documents/{document_id}/reading-runs', response_model=Run, status_code=202)
    def start(document_id: UUID, request: ReadingRequest, http_request: Request):
        try:
            return http_request.app.state.reading_worker.start(document_id, request.mode, request.section, request.query)
        except ModelError as exc:
            raise HTTPException(503, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('/api/documents/{document_id}/reading-runs', response_model=list[Run])
    def runs(document_id: UUID):
        with closing(db.connect(settings.db_path)) as conn:
            return [run_from_row(row) for row in conn.execute('SELECT * FROM runs WHERE document_id=? ORDER BY created_at DESC LIMIT 20', (str(document_id),))]

    @router.get('/api/reading-runs/{run_id}', response_model=Run)
    def status(run_id: UUID):
        try:
            return get_run(settings, run_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get('/api/documents/{document_id}/notes', response_model=list[Note])
    def notes(document_id: UUID):
        return list_notes(settings, document_id)

    @router.post('/api/documents/{document_id}/notes', response_model=Note, status_code=201)
    def edit(document_id: UUID, request: ManualRequest):
        try:
            return save_manual(settings, document_id, request.base_note_id, request.changes)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
