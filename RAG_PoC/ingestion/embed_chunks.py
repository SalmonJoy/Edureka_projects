"""Embed chunk JSONL with local Ollama and save chunks, metadata, and vectors."""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHUNKS = PROJECT_ROOT / "source" / "chunks" / "RAG demo.jsonl"
EMBEDDINGS_DIR = PROJECT_ROOT / "source" / "embeddings"
OLLAMA_URL = "http://localhost:11434"
MODEL = "nomic-embed-text:latest"
DIMENSIONS = 768
DOCUMENT_PREFIX = "search_document: "


def load_chunks(chunks_path: Path) -> list[dict]:
    """Validate the input before making any embedding requests."""
    if not chunks_path.is_file():
        raise FileNotFoundError(f"Chunks file not found: {chunks_path}")
    if chunks_path.suffix.lower() != ".jsonl":
        raise ValueError(f"Expected a .jsonl file: {chunks_path}")

    chunks = []
    seen_ids = set()
    with chunks_path.open(encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc.msg}") from exc
            if not isinstance(chunk, dict):
                raise ValueError(f"Line {line_number} must contain a JSON object")
            for field in ("chunk_id", "content", "contextualized_content"):
                if not isinstance(chunk.get(field), str) or not chunk[field].strip():
                    raise ValueError(f"Line {line_number} needs a nonempty '{field}' string")
            if chunk["chunk_id"] in seen_ids:
                raise ValueError(f"Duplicate chunk_id on line {line_number}: {chunk['chunk_id']}")
            seen_ids.add(chunk["chunk_id"])
            chunks.append(chunk)

    if not chunks:
        raise ValueError(f"No chunks found in: {chunks_path}")
    return chunks


def embed_texts(
    texts: list[str], *, ollama_url: str = OLLAMA_URL, timeout: float = 120
) -> list[list[float]]:
    """Request document embeddings in input order, without silent truncation."""
    endpoint = f"{ollama_url.rstrip('/')}/api/embed"
    payload = {
        "model": MODEL,
        "input": [DOCUMENT_PREFIX + text for text in texts],
        "truncate": False,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Ollama request failed at {endpoint}: {exc}. "
            "Check that Ollama is running, or increase --timeout."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Ollama returned an invalid JSON response") from exc

    vectors = result.get("embeddings") if isinstance(result, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise RuntimeError("Ollama returned an unexpected number of embeddings")
    for vector in vectors:
        if (
            not isinstance(vector, list)
            or len(vector) != DIMENSIONS
            or not all(type(value) in (int, float) and math.isfinite(value) for value in vector)
            or not any(vector)
        ):
            raise RuntimeError(f"Ollama must return nonzero, finite {DIMENSIONS}-dimension vectors")
    return vectors


def embed_chunks_file(
    chunks_path: Path,
    *,
    ollama_url: str = OLLAMA_URL,
    batch_size: int = 8,
    timeout: float = 120,
) -> Path:
    """Embed contextualized_content and preserve every original chunk field."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")

    chunks_path = Path(chunks_path).expanduser().resolve()
    output_path = EMBEDDINGS_DIR / f"{chunks_path.stem}.jsonl"
    if chunks_path == output_path.resolve():
        raise ValueError("Input must be a chunks file, not the destination embeddings file")
    chunks = load_chunks(chunks_path)
    embedded_chunks = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        try:
            vectors = embed_texts(
                [chunk["contextualized_content"] for chunk in batch],
                ollama_url=ollama_url,
                timeout=timeout,
            )
        except RuntimeError as exc:
            ids = ", ".join(chunk["chunk_id"] for chunk in batch)
            raise RuntimeError(f"Embedding failed for chunks [{ids}]: {exc}") from exc

        for chunk, vector in zip(batch, vectors):
            embedded_chunks.append(
                {
                    **chunk,
                    "embedding": vector,
                    "embedding_metadata": {
                        "provider": "ollama",
                        "model": MODEL,
                        "dimensions": DIMENSIONS,
                        "input_field": "contextualized_content",
                        "prefix": DOCUMENT_PREFIX,
                    },
                }
            )
        print(f"Embedded {len(embedded_chunks)}/{len(chunks)} chunks")

    # Publish only a complete JSONL file. A failed request or save leaves an
    # existing output untouched; the temporary file is closed before Windows rename.
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=EMBEDDINGS_DIR,
            prefix=f"{chunks_path.stem}-", suffix=".tmp", delete=False,
        ) as output:
            temporary_path = Path(output.name)
            for chunk in embedded_chunks:
                output.write(json.dumps(chunk, ensure_ascii=False, allow_nan=False) + "\n")
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chunks_path", nargs="?", type=Path, default=DEFAULT_CHUNKS,
                        help="Input JSONL (default: source/chunks/RAG demo.jsonl)")
    parser.add_argument("--ollama-url", default=OLLAMA_URL, help="Ollama server base URL")
    parser.add_argument("--batch-size", type=int, default=8, help="Chunks per request (default: 8)")
    parser.add_argument("--timeout", type=float, default=120, help="Seconds per request (default: 120)")
    args = parser.parse_args()
    try:
        output_path = embed_chunks_file(
            args.chunks_path, ollama_url=args.ollama_url,
            batch_size=args.batch_size, timeout=args.timeout,
        )
    except Exception as exc:
        print(f"Embedding failed: {exc}", file=sys.stderr)
        return 1
    print(f"Embeddings saved to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
