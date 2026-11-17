from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.config import Settings
from app.schemas import Schema
from app.services import dedup


class PairRequest(Schema):
    left_id: UUID
    right_id: UUID
    token: str = Field(pattern=r'^[0-9a-f]{64}$')


class RelationRequest(PairRequest):
    kind: Literal['version', 'related', 'distinct'] | None


class MergeRequest(PairRequest):
    fields: dict[str, Literal['left', 'right']]


def dedup_router(settings: Settings):
    router = APIRouter()

    def invoke(operation, *args):
        try:
            return operation(settings, *args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('/api/papers/{paper_id}/duplicates')
    def candidates(paper_id: UUID, include_distinct: bool = False):
        return invoke(dedup.candidates, paper_id, include_distinct)

    @router.get('/api/paper-comparison')
    def compare(left_id: UUID, right_id: UUID):
        return invoke(dedup.compare, left_id, right_id)

    @router.put('/api/paper-relations')
    def relation(request: RelationRequest):
        return invoke(dedup.set_relation, request.left_id, request.right_id, request.token, request.kind)

    @router.post('/api/merges', status_code=201)
    def merge(request: MergeRequest):
        return invoke(dedup.merge, request.left_id, request.right_id, request.token, request.fields)

    @router.get('/api/papers/{paper_id}/merges')
    def history(paper_id: UUID):
        return invoke(dedup.history, paper_id)

    @router.post('/api/merges/{event_id}/undo')
    def undo(event_id: UUID):
        return invoke(dedup.undo, event_id)

    return router
