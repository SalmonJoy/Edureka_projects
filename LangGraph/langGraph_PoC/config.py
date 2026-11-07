"""Configuration shared by the graph and its service clients."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True)
class Settings:
    ollama_api_key: str = field(repr=False)
    ollama_url: str = "https://ollama.com"
    ollama_model: str = "gpt-oss:20b"
    employee_id: str = "EMP004"
    employee_url: str = "http://127.0.0.1:8000"
    policy_url: str = "http://127.0.0.1:8001"
    leave_url: str = "http://127.0.0.1:8002"
    api_timeout: float = 10
    ollama_timeout: float = 120


def load_settings(env_file: Path = ENV_FILE) -> Settings:
    values = dotenv_values(env_file) if env_file.is_file() else {}
    # The explicitly requested .env wins over a different inherited key.
    key = (values.get("OLLAMA_API_KEY") or os.getenv("OLLAMA_API_KEY", "")).strip()
    if not key:
        raise ValueError(f"Missing OLLAMA_API_KEY. Set it in {env_file}.")
    from langGraph_PoC.tracing import configure_tracing

    configure_tracing(env_file)
    return Settings(ollama_api_key=key)
