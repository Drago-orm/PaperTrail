from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from app.config import Settings


class ModelStatus(BaseModel):
    configured: bool
    missing: list[str]
    errors: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model: ModelStatus


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()
    application = FastAPI(title="研迹 PaperTrail", version="0.1.0")

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
