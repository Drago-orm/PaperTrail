import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    model_base_url: str = ""
    model_name: str = ""
    model_api_key: str = field(default="", repr=False)
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    model_enable_thinking: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "papertrail.sqlite3"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls(
            model_base_url=os.getenv("PAPERTRAIL_MODEL_BASE_URL", "").strip(),
            model_name=os.getenv("PAPERTRAIL_MODEL_NAME", "").strip(),
            model_api_key=os.getenv("PAPERTRAIL_MODEL_API_KEY", "").strip(),
            model_enable_thinking=os.getenv("PAPERTRAIL_MODEL_ENABLE_THINKING", "").strip().lower(),
        )

    def model_options(self) -> dict[str, bool]:
        if not self.model_enable_thinking:
            return {}
        return {"enable_thinking": self.model_enable_thinking == "true"}

    def model_status(self) -> tuple[list[str], list[str]]:
        values = {
            "PAPERTRAIL_MODEL_BASE_URL": self.model_base_url,
            "PAPERTRAIL_MODEL_NAME": self.model_name,
            "PAPERTRAIL_MODEL_API_KEY": self.model_api_key,
        }
        missing = [name for name, value in values.items() if not value]
        errors: list[str] = []
        if self.model_enable_thinking not in {"", "true", "false"}:
            errors.append("PAPERTRAIL_MODEL_ENABLE_THINKING 必须留空或填写 true／false")
        if self.model_base_url:
            try:
                url = TypeAdapter(AnyHttpUrl).validate_python(self.model_base_url)
                valid_url = (
                    self.model_base_url.lower().startswith(("http://", "https://"))
                    and url.username is None
                    and url.password is None
                )
            except ValidationError:
                valid_url = False
            if not valid_url:
                errors.append(
                    "PAPERTRAIL_MODEL_BASE_URL 必须是无凭据的绝对 HTTP(S) URL"
                )
        return missing, errors
