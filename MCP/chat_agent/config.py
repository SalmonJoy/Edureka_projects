"""Load the shared .env and keep the agent's settings in one place."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True)
class Settings:
    ollama_api_key: str = field(default="", repr=False)
    ollama_host: str = "https://ollama.com"
    ollama_model: str = "gpt-oss:20b"
    mcp_url: str = "http://127.0.0.1:8003/mcp"
    model_provider: str = "ollama"
    openrouter_api_key: str = field(default="", repr=False)
    openrouter_host: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openrouter/free"


def load_settings(env_file: Path = ENV_FILE) -> Settings:
    # The file explicitly chosen for this project wins over inherited values.
    load_dotenv(env_file, override=True)
    key = os.getenv("OLLAMA_API_KEY", "").strip()
    router_key = (
        os.getenv("OPEN_ROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or ""
    ).strip()
    provider = os.getenv(
        "MODEL_PROVIDER", "openrouter" if router_key else "ollama"
    ).strip().lower()
    if provider not in {"ollama", "openrouter"}:
        raise ValueError("MODEL_PROVIDER must be ollama or openrouter.")
    if provider == "ollama" and not key:
        raise ValueError(f"Missing OLLAMA_API_KEY. Set it in {env_file}.")
    if provider == "openrouter" and not router_key:
        raise ValueError(f"Missing OPEN_ROUTER_API_KEY. Set it in {env_file}.")

    port = os.getenv("MCP_PORT", "8003")
    return Settings(
        ollama_api_key=key,
        ollama_host=os.getenv("OLLAMA_HOST", "https://ollama.com"),
        ollama_model=os.getenv("OLLAMA_MODEL", "gpt-oss:20b"),
        mcp_url=os.getenv("MCP_URL", f"http://127.0.0.1:{port}/mcp"),
        model_provider=provider,
        openrouter_api_key=router_key,
        openrouter_host=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openrouter/free"),
    )
