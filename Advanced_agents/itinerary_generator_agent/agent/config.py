"""Read the shared .env explicitly; do not modify files or global environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = field(repr=False)
    qdrant_api_key: str = field(repr=False)
    qdrant_url: str
    model: str = "openai/gpt-oss-20b"
    openrouter_url: str = "https://openrouter.ai/api/v1"
    ollama_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "nomic-embed-text:latest"
    collection: str = "Edureka"
    travel_url: str = "http://127.0.0.1:8000"
    max_replans: int = 3


def load_settings(env_file: Path = ENV_FILE) -> Settings:
    values = dotenv_values(env_file) if env_file.is_file() else {}

    def read(name: str, default: str = "") -> str:
        # The requested file wins over a stale key inherited by the process.
        return str(values.get(name) or os.getenv(name) or default).strip()

    key = read("OPEN_ROUTER_API_KEY") or read("OPENROUTER_API_KEY")
    endpoint = read("QDRANT_ENDPOINT")
    qdrant_key = read("QDRANT_API_KEY")
    if not key or not endpoint or not qdrant_key:
        raise ValueError(f"Set OPEN_ROUTER_API_KEY, QDRANT_ENDPOINT, and QDRANT_API_KEY in {env_file}.")
    return Settings(
        openrouter_api_key=key, qdrant_api_key=qdrant_key, qdrant_url=endpoint,
        model=read("OPENROUTER_MODEL", "openai/gpt-oss-20b"),
        ollama_url=read("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        embedding_model=read("OLLAMA_EMBED_MODEL", "nomic-embed-text:latest"),
        collection=read("QDRANT_COLLECTION", "Edureka"),
        travel_url=read("TRAVEL_API_URL", "http://127.0.0.1:8000"),
    )
