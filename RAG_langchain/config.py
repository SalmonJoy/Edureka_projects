"""Shared settings for the two learning chains."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PDF = PROJECT_ROOT.parent / "RAG_PoC" / "source" / "pdf" / "RAG demo.pdf"


@dataclass(frozen=True)
class Settings:
    pinecone_api_key: str = field(repr=False)
    pinecone_index_name: str
    pinecone_host: str
    cloud_api_key: str = field(default="", repr=False)
    namespace: str = "testdata-langchain"
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text:latest"
    embedding_dimensions: int = 768
    cloud_url: str = "https://ollama.com"
    cloud_model: str = "gpt-oss:120b"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k: int = 3
    similarity_threshold: float = 0.6


def load_settings() -> Settings:
    """Read project/shared .env files; keep this example's namespace separate."""
    shared = dotenv_values(PROJECT_ROOT.parent / ".env")
    project = dotenv_values(PROJECT_ROOT / ".env")
    values = {**shared, **project, **os.environ}
    required = ("PINECONE_API_KEY", "PINECONE_INDEX_NAME", "PINECONE_HOST")
    missing = [name for name in required if not (values.get(name) or "").strip()]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    host = values["PINECONE_HOST"].strip().rstrip("/")
    if "://" not in host:
        host = "https://" + host
    parsed = urlparse(host)
    if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".pinecone.io")
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("PINECONE_HOST must be an HTTPS *.pinecone.io index host")
    namespace = (values.get("PINECONE_LANGCHAIN_NAMESPACE") or "testdata-langchain").strip()
    if not namespace:
        raise ValueError("PINECONE_LANGCHAIN_NAMESPACE must not be blank")
    # The .env cloud key wins over the different key inherited by this machine.
    cloud_key = (project.get("OLLAMA_API_KEY") or shared.get("OLLAMA_API_KEY")
                 or os.getenv("OLLAMA_API_KEY", "")).strip()
    return Settings(
        pinecone_api_key=values["PINECONE_API_KEY"].strip(),
        pinecone_index_name=values["PINECONE_INDEX_NAME"].strip(),
        pinecone_host=host, cloud_api_key=cloud_key, namespace=namespace,
    )
