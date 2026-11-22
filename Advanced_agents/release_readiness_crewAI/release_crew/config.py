"""Read only the relevant values from the shared .env; never print API keys."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = field(default="", repr=False)
    model: str = "openai/gpt-oss-20b"
    openrouter_url: str = "https://openrouter.ai/api/v1"
    shopflow_url: str = "http://127.0.0.1:8001"
    model_timeout: int = 120
    max_iterations: int = 8


def load_settings(env_file: Path = ENV_FILE) -> Settings:
    values = dotenv_values(env_file) if env_file.is_file() else {}

    def read(name: str, default: str = "") -> str:
        return str(values.get(name) or os.getenv(name) or default).strip()

    return Settings(
        openrouter_api_key=read("OPEN_ROUTER_API_KEY") or read("OPENROUTER_API_KEY"),
        model=read("READINESS_MODEL") or read("OPENROUTER_MODEL", "openai/gpt-oss-20b"),
        shopflow_url=read("SHOPFLOW_API_URL", "http://127.0.0.1:8001"),
    )
