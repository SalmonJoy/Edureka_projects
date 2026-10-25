"""Upsert embeddings JSONL with chunk text and metadata into Pinecone."""

import argparse
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EMBEDDINGS = PROJECT_ROOT / "source" / "embeddings" / "RAG demo.jsonl"
API_VERSION = "2025-10"


@dataclass(frozen=True)
class PineconeConfig:
    api_key: str = field(repr=False)
    index_name: str
    host: str
    namespace: str


def load_config(env_file: Path | None = None) -> PineconeConfig:
    """Use process settings ahead of the project .env, then the parent .env."""
    if env_file is not None:
        env_file = Path(env_file).expanduser().resolve()
        if not env_file.is_file():
            raise FileNotFoundError(f"Environment file not found: {env_file}")
        settings = dict(dotenv_values(env_file))
    else:
        settings = {
            **dotenv_values(PROJECT_ROOT.parent / ".env"),
            **dotenv_values(PROJECT_ROOT / ".env"),
        }
    settings.update(os.environ)
    names = ("PINECONE_API_KEY", "PINECONE_INDEX_NAME", "PINECONE_HOST", "PINECONE_NAMESPACE")
    missing = [name for name in names if not settings.get(name, "") or not settings[name].strip()]
    if missing:
        raise ValueError("Missing Pinecone settings: " + ", ".join(missing))

    host = settings["PINECONE_HOST"].strip().rstrip("/")
    if "://" not in host:
        host = "https://" + host
    parsed = urlparse(host)
    if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".pinecone.io")
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)):
        raise ValueError("PINECONE_HOST must be an HTTPS *.pinecone.io index host")
    return PineconeConfig(settings[names[0]].strip(), settings[names[1]].strip(),
                          host, settings[names[3]].strip())


def encode_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def prepare_vectors(embeddings_path: Path) -> list[dict]:
    """Validate every row and adapt nested embedding metadata for Pinecone."""
    if not embeddings_path.is_file():
        raise FileNotFoundError(f"Embeddings file not found: {embeddings_path}")
    if embeddings_path.suffix.lower() != ".jsonl":
        raise ValueError(f"Expected a .jsonl file: {embeddings_path}")
    vectors = []
    seen_ids = set()
    dimensions = None
    with embeddings_path.open(encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Line {line_number} must contain a JSON object")
            for name in ("chunk_id", "content", "contextualized_content"):
                if not isinstance(record.get(name), str) or not record[name].strip():
                    raise ValueError(f"Line {line_number} needs a nonempty '{name}' string")
            chunk_id = record["chunk_id"]
            if len(chunk_id) > 512 or chunk_id in seen_ids:
                raise ValueError(f"Line {line_number}: duplicate or overlong chunk_id: {chunk_id}")
            seen_ids.add(chunk_id)
            values = record.get("embedding")
            if (not isinstance(values, list) or not values
                    or not all(type(value) in (int, float) and math.isfinite(value) for value in values)
                    or not any(values)):
                raise ValueError(f"Line {line_number}: embedding must be a finite, nonzero numeric vector")
            if dimensions is None:
                dimensions = len(values)
            if len(values) != dimensions:
                raise ValueError(f"Line {line_number}: inconsistent embedding dimensions")

            metadata = {key: value for key, value in record.items()
                        if key not in ("embedding", "embedding_metadata")}
            embedding_metadata = record.get("embedding_metadata", {})
            if not isinstance(embedding_metadata, dict):
                raise ValueError(f"Line {line_number}: embedding_metadata must be an object")
            if embedding_metadata.get("dimensions", dimensions) != dimensions:
                raise ValueError(f"Line {line_number}: embedding metadata dimension does not match vector")
            for key, value in embedding_metadata.items():
                target_key = f"embedding_{key}"
                if target_key in metadata:
                    raise ValueError(f"Line {line_number}: metadata field collision: {target_key}")
                metadata[target_key] = value
            for key, value in metadata.items():
                valid = (isinstance(value, (str, bool))
                         or (type(value) in (int, float) and math.isfinite(value))
                         or (isinstance(value, list) and all(isinstance(item, str) for item in value)))
                if not key or key.startswith("$") or not valid:
                    raise ValueError(f"Line {line_number}: unsupported Pinecone metadata field '{key}'")
            if len(encode_json(metadata)) > 40_000:
                raise ValueError(f"Line {line_number}: metadata exceeds 40 KB; split this chunk first")
            vectors.append({"id": chunk_id, "values": values, "metadata": metadata})
    if not vectors:
        raise ValueError(f"No embeddings found in: {embeddings_path}")
    return vectors


def pinecone_request(url: str, api_key: str, body: dict | None = None, *, timeout: float = 30) -> dict:
    """Call the REST API without logging credentials."""
    request = Request(url, data=encode_json(body) if body is not None else None,
                      headers={"Api-Key": api_key, "Content-Type": "application/json",
                               "X-Pinecone-Api-Version": API_VERSION},
                      method="POST" if body is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").replace(api_key, "[REDACTED]")
        raise RuntimeError(f"Pinecone HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Pinecone connection failed: {str(exc).replace(api_key, '[REDACTED]')}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Pinecone returned an invalid JSON response") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Pinecone returned an unexpected response")
    return result


def upload_embeddings(
    embeddings_path: Path, *, env_file: Path | None = None,
    batch_size: int = 50, timeout: float = 30, dry_run: bool = False,
) -> int:
    """Validate the file and index, then upsert records; return the record count."""
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    embeddings_path = Path(embeddings_path).expanduser().resolve()
    vectors = prepare_vectors(embeddings_path)
    config = load_config(env_file)
    batches = [{"namespace": config.namespace, "vectors": vectors[start:start + batch_size]}
               for start in range(0, len(vectors), batch_size)]
    if any(len(encode_json(batch)) > 2_000_000 for batch in batches):
        raise ValueError("An upsert batch exceeds 2 MB; reduce --batch-size")

    index = pinecone_request("https://api.pinecone.io/indexes/" + quote(config.index_name, safe=""),
                             config.api_key, timeout=timeout)
    if not index.get("status", {}).get("ready"):
        raise ValueError(f"Pinecone index '{config.index_name}' is not ready")
    if index.get("vector_type", "dense") != "dense":
        raise ValueError("The target index must support dense vectors")
    if index.get("dimension") != len(vectors[0]["values"]):
        raise ValueError(f"Index dimension {index.get('dimension')} does not match "
                         f"embedding dimension {len(vectors[0]['values'])}")
    if urlparse(config.host).hostname != index.get("host"):
        raise ValueError("PINECONE_HOST does not match PINECONE_INDEX_NAME")
    print(f"Target: index={config.index_name}, namespace={config.namespace}")
    if dry_run:
        print(f"Validated {len(vectors)} records; dry run does not upload")
        return len(vectors)

    uploaded = 0
    for batch in batches:
        try:
            response = pinecone_request(config.host + "/vectors/upsert", config.api_key,
                                         batch, timeout=timeout)
            if response.get("upsertedCount") != len(batch["vectors"]):
                raise RuntimeError("Pinecone returned an unexpected upsertedCount")
        except RuntimeError as exc:
            raise RuntimeError(f"Upload stopped after {uploaded}/{len(vectors)} acknowledged records. "
                               f"Rerun safely with the same chunk IDs. {exc}") from exc
        uploaded += len(batch["vectors"])
        print(f"Upserted {uploaded}/{len(vectors)} records")
    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("embeddings_path", nargs="?", type=Path, default=DEFAULT_EMBEDDINGS,
                        help="Input JSONL (default: source/embeddings/RAG demo.jsonl)")
    parser.add_argument("--env-file", type=Path, help="Explicit .env file; process settings still take priority")
    parser.add_argument("--batch-size", type=int, default=50, help="Records per request (default: 50)")
    parser.add_argument("--timeout", type=float, default=30, help="Seconds per request (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Validate file and remote index without uploading")
    args = parser.parse_args()
    try:
        count = upload_embeddings(args.embeddings_path, env_file=args.env_file,
                                  batch_size=args.batch_size, timeout=args.timeout, dry_run=args.dry_run)
    except Exception as exc:
        print(f"Pinecone upload failed: {exc}", file=sys.stderr)
        return 1
    if not args.dry_run:
        print(f"Upload complete: {count} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
