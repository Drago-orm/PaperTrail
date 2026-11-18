from typing import Literal
from uuid import UUID
import sqlite3

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import Field, ValidationError, model_validator

from app.schemas import Schema
from app.services import bibliography


class ExportRequest(Schema):
    format: Literal['bib', 'ris']
    paper_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_ids(self):
        if len(set(self.paper_ids)) != len(self.paper_ids): raise ValueError('文献ID不能重复')
        return self


def bibliography_router(settings):
    router = APIRouter()

    def invoke(operation, *args):
        try:
            return operation(settings, *args)
        except ValidationError as exc:
            raise HTTPException(422, '导入字段不满足文献约束；请核对DOI长度、标识符、作者和年份') from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except LookupError as exc:
            if type(exc) is not LookupError: raise
            raise HTTPException(404, str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(500, '元数据文件或数据库无法读写；导入事务未完成，请检查权限和磁盘') from exc

    @router.post('/api/bibliography/preview')
    def preview(file: UploadFile, format: Literal['bib', 'ris'] = Form()):
        return invoke(bibliography.exchange, file.file.read(bibliography.MAX_BYTES + 1), format)

    @router.post('/api/bibliography/import', status_code=201)
    def import_file(file: UploadFile, format: Literal['bib', 'ris'] = Form(), confirmation_token: str = Form(pattern=r'^[0-9a-f]{64}$')):
        return invoke(bibliography.exchange, file.file.read(bibliography.MAX_BYTES + 1), format, confirmation_token)

    @router.post('/api/bibliography/export')
    def export(request: ExportRequest):
        return invoke(bibliography.export, request.paper_ids, request.format)

    return router
