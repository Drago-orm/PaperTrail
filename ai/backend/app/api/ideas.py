from contextlib import closing
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app import db
from app.agents.ideas import IdeaDraft, SourceChoice
from app.agents.client import ModelError
from app.schemas import Schema
from app.services import ideas


class GenerateRequest(Schema):
    sources: list[SourceChoice] = Field(min_length=1, max_length=6)
    interest: str = Field(default='', max_length=1000)
    resources: str = Field(default='', max_length=1000)


class SaveRequest(Schema):
    draft: IdeaDraft
    base_idea_id: UUID | None = None
    status: Literal['draft','confirmed','testing','archived'] = 'draft'


def ideas_router(settings):
    router = APIRouter()

    @router.get('/api/idea-sources')
    def sources():
        try:
            return ideas.available(settings)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/api/idea-runs', status_code=202)
    def generate(request: GenerateRequest, http_request: Request):
        try:
            conditions = {key: getattr(request,key).replace('\r\n','\n').strip() for key in ('interest','resources')}
            return http_request.app.state.reading_worker.start_idea(request.sources, conditions)
        except ModelError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('/api/ideas')
    def listing(q: str = '', status: Literal['','draft','confirmed','testing','archived'] = ''):
        return ideas.listing(settings,q,status)

    @router.get('/api/ideas/{idea_id}')
    def detail(idea_id: UUID):
        with closing(db.connect(settings.db_path)) as conn:
            row = conn.execute('SELECT * FROM ideas WHERE id=?',(str(idea_id),)).fetchone()
            if row is None:
                raise HTTPException(404,'Idea版本不存在')
            return ideas.view(conn,row)

    @router.get('/api/ideas/{idea_id}/versions')
    def versions(idea_id: UUID):
        with closing(db.connect(settings.db_path)) as conn:
            root = conn.execute('SELECT root_id FROM ideas WHERE id=?',(str(idea_id),)).fetchone()
            if root is None:
                raise HTTPException(404,'Idea版本不存在')
            return [ideas.view(conn,row) for row in conn.execute('SELECT * FROM ideas WHERE root_id=? ORDER BY version DESC',(root['root_id'],))]

    @router.post('/api/ideas', status_code=201)
    def save(request: SaveRequest):
        try:
            idea = ideas.save(settings,request.draft,request.base_idea_id,request.status)
            with closing(db.connect(settings.db_path)) as conn:
                return ideas.view(conn,conn.execute('SELECT * FROM ideas WHERE id=?',(str(idea.id),)).fetchone())
        except LookupError as exc:
            raise HTTPException(404,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
    return router
