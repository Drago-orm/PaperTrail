import importlib

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_application_import_and_health_without_model_configuration():
    assert importlib.import_module("app.main").app is not None
    with TestClient(create_app(Settings())) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model": {
            "configured": False,
            "missing": [
                "PAPERTRAIL_MODEL_BASE_URL",
                "PAPERTRAIL_MODEL_NAME",
                "PAPERTRAIL_MODEL_API_KEY",
            ],
            "errors": [],
        },
    }


def test_configured_model_status_does_not_leak_credentials():
    secret = "test-only-secret"
    settings = Settings("https://model.example/v1", "test-model", secret)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["model"] == {
        "configured": True,
        "missing": [],
        "errors": [],
    }
    assert secret not in response.text
    assert secret not in repr(settings)


@pytest.mark.parametrize(
    "base_url",
    [
        "relative/path",
        "https:example.com",
        "file:///tmp/model",
        "https://",
        "https://host:invalid",
        "https://bad host",
        "https://user:secret@host",
    ],
)
def test_invalid_model_url_is_reported_without_preventing_startup(base_url):
    settings = Settings(base_url, "test-model", "test-only-secret")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    status = response.json()["model"]
    assert status["configured"] is False
    assert status["missing"] == []
    assert status["errors"] == [
        "PAPERTRAIL_MODEL_BASE_URL 必须是无凭据的绝对 HTTP(S) URL"
    ]
    assert "secret" not in response.text


def test_environment_overrides_dotenv_and_blank_values_are_missing(monkeypatch):
    monkeypatch.setenv("PAPERTRAIL_MODEL_BASE_URL", "  https://model.example/v1  ")
    monkeypatch.setenv("PAPERTRAIL_MODEL_NAME", "  ")
    monkeypatch.setenv("PAPERTRAIL_MODEL_API_KEY", "  test-key  ")
    settings = Settings.from_env()
    assert settings.model_base_url == "https://model.example/v1"
    assert settings.model_api_key == "test-key"
    assert settings.model_status() == (["PAPERTRAIL_MODEL_NAME"], [])
