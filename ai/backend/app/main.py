from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from fastapi import FastAPI, UploadFile, Form, HTTPException
from uuid import UUID
from pydantic import BaseModel

from app.config import Settings
from app.api.library import library_router
from app.api.reading import reading_router
from app.api.ideas import ideas_router
from app.api.exports import export_router
from app.api.dedup import dedup_router
from app.api.comparisons import comparison_router
from app.api.translation import translation_router
from app.api.bibliography import bibliography_router
from app.services.reading_runs import ReadingWorker
from app.db import initialize
from app.services.pdf_import import ImportRejected, ImportResult, get_result, import_pdf


class ModelStatus(BaseModel):
    configured: bool
    missing: list[str]
    errors: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model: ModelStatus


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        initialize(settings.db_path)
        application.state.reading_worker = ReadingWorker(settings)
        try:
            yield
        finally:
            application.state.reading_worker.close()

    application = FastAPI(title="研迹 PaperTrail", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.include_router(library_router(settings))
    application.include_router(reading_router(settings))
    application.include_router(ideas_router(settings))
    application.include_router(export_router(settings))
    application.include_router(dedup_router(settings))
    application.include_router(comparison_router(settings))
    application.include_router(translation_router(settings))
    application.include_router(bibliography_router(settings))

    @application.post('/api/documents', response_model=ImportResult)
    def upload_document(file: UploadFile, paper_id: UUID | None = Form(default=None)) -> ImportResult:
        try:
            return import_pdf(settings, file.file, file.filename or 'unnamed.pdf', paper_id)
        except ImportRejected as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        finally:
            file.file.close()

    @application.get('/api/documents/{document_id}', response_model=ImportResult)
    def read_document(document_id: UUID) -> ImportResult:
        try:
            return get_result(settings, document_id)
        except (LookupError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @application.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        missing, errors = settings.model_status()
        return HealthResponse(
            status="ok",
            model=ModelStatus(
                configured=not missing and not errors,
                missing=missing,
                errors=errors,
            ),
        )

    return application


app = create_app()
