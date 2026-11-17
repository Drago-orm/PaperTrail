from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field, model_validator

from app.config import Settings
from app.schemas import Schema
from app.services import exports


class ExportRequest(Schema):
    note_id: UUID | None = None
    idea_ids: list[UUID] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def selection(self):
        if bool(self.note_id) == bool(self.idea_ids):
            raise ValueError('请选择一篇已保存笔记，或1—10个Idea版本')
        if len(set(self.idea_ids)) != len(self.idea_ids):
            raise ValueError('Idea版本不能重复')
        return self


def export_router(settings: Settings):
    router = APIRouter()

    @router.post('/api/exports', status_code=201)
    def create(request: ExportRequest):
        try:
            return exports.create(settings, request.note_id, request.idea_ids)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, '导出文件写入失败，请检查数据目录权限和磁盘空间') from exc

    @router.get('/api/exports/{export_id}/{format}')
    def download(export_id: UUID, format: Literal['md', 'docx']):
        try:
            path = exports.download(settings, export_id, format)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, '导出文件无法读取，请检查数据目录') from exc
        media = 'text/markdown; charset=utf-8' if format == 'md' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        return FileResponse(path, media_type=media, filename=f'papertrail-{export_id}.{format}')

    return router
