from fastapi import APIRouter, HTTPException

from app.agents.client import ModelError
from app.services import translation
from app.services.translation import PreviewRequest, TranslateRequest


def translation_router(settings):
    router = APIRouter()

    def invoke(operation, request):
        try:
            return operation(settings, request)
        except ModelError as exc:
            raise HTTPException(503, str(exc)) from exc
        except LookupError as exc:
            # Only the explicit missing-record signal is 404; IndexError/KeyError are internal faults.
            if type(exc) is not LookupError:
                raise
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, '翻译缓存无法读写，本次范围未完成；已保存的合法缓存可在下次复用，请检查数据目录') from exc

    @router.post('/api/translations/preview')
    def preview(request: PreviewRequest):
        return invoke(translation.preview, request)

    @router.post('/api/translations')
    def translate(request: TranslateRequest):
        return invoke(translation.translate, request)

    return router
