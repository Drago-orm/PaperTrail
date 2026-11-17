from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.services import comparisons
from app.services.comparisons import ComparisonRequest


def comparison_router(settings):
    router = APIRouter()

    def invoke(operation, *args):
        try:
            return operation(settings, *args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, '比较快照文件无法读写，请检查数据目录权限和磁盘空间') from exc

    @router.post('/api/study-comparisons', status_code=201)
    def create(request: ComparisonRequest):
        return invoke(comparisons.create, request)

    @router.get('/api/study-comparisons/{key}')
    def read(key: UUID):
        return invoke(comparisons.load, key)

    return router
