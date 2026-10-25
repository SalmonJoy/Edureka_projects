"""Check Pinecone mapping and preflight checks without writing cloud records."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from ingestion.upload_to_pinecone import (
    PineconeConfig, load_config, pinecone_request, prepare_vectors, upload_embeddings,
)


CONFIG = PineconeConfig("test-secret-key", "rag-poc", "https://example.svc.pinecone.io", "testdata")
INDEX = {"dimension": 2, "status": {"ready": True}, "vector_type": "dense",
         "host": "example.svc.pinecone.io"}


def make_record(index=1):
    return {"chunk_id": f"guide-{index}", "source": "guide.md", "content": f"Café {index}",
            "contextualized_content": f"Guide > Setup\nCafé {index}", "heading_path": ["Guide", "Setup"],
            "embedding": [0.2, 0.4], "heading_level": 2,
            "embedding_metadata": {"model": "nomic-embed-text:latest", "dimensions": 2,
                                   "prefix": "search_document: "}}


class PineconeUploaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "sample file.jsonl"

    def write_records(self, records):
        self.source.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records),
                               encoding="utf-8")

    def test_mapping_preserves_text_heading_metadata_and_vectors(self):
        record = make_record()
        self.write_records([record])
        original = self.source.read_bytes()
        vector = prepare_vectors(self.source)[0]
        self.assertEqual(vector["id"], record["chunk_id"])
        self.assertEqual(vector["values"], record["embedding"])
        for key, value in record.items():
            if key not in ("embedding", "embedding_metadata"):
                self.assertEqual(vector["metadata"][key], value)
        self.assertEqual(vector["metadata"]["embedding_model"], "nomic-embed-text:latest")
        self.assertEqual(vector["metadata"]["embedding_dimensions"], 2)
        self.assertEqual(vector["metadata"]["embedding_prefix"], "search_document: ")
        self.assertNotIn("embedding_metadata", vector["metadata"])
        self.assertEqual(self.source.read_bytes(), original)

    def test_invalid_records_are_rejected(self):
        cases = [[], [make_record(), make_record()],
                 [{**make_record(), "embedding": [float("nan"), 0.1]}],
                 [{**make_record(), "embedding": [True, 0.1]}],
                 [{**make_record(), "embedding": [0, 0]}],
                 [{**make_record(), "embedding": [0.1]}],
                 [{**make_record(), "content": ""}],
                 [{**make_record(), "extra": {"nested": "unsupported"}}],
                 [{**make_record(), "extra": None}],
                 [{**make_record(), "content": "x" * 40_001}],
                 [{**make_record(), "embedding_model": "collision"}]]
        for rows in cases:
            with self.subTest(rows=str(rows)[:80]):
                self.write_records(rows)
                with self.assertRaises(ValueError):
                    prepare_vectors(self.source)
        self.source.write_text("not JSON", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 1"):
            prepare_vectors(self.source)
        with self.assertRaises(FileNotFoundError):
            prepare_vectors(self.source.parent / "missing.jsonl")

    def test_config_precedence_host_validation_and_secret_repr(self):
        root = Path(self.temp.name) / "project"
        root.mkdir()
        (root.parent / ".env").write_text(
            "PINECONE_API_KEY=test-secret-key\nPINECONE_INDEX_NAME=parent-index\n"
            "PINECONE_HOST=example.svc.pinecone.io\nPINECONE_NAMESPACE=parent-namespace\n",
            encoding="utf-8",
        )
        (root / ".env").write_text("PINECONE_NAMESPACE=project-namespace\n", encoding="utf-8")
        with patch("ingestion.upload_to_pinecone.PROJECT_ROOT", root), patch.dict(os.environ, {}, clear=True):
            config = load_config()
            self.assertEqual(config.namespace, "project-namespace")
            self.assertEqual(config.host, CONFIG.host)
            self.assertNotIn("test-secret-key", repr(config))
            with patch.dict(os.environ, {"PINECONE_NAMESPACE": "process-namespace"}):
                self.assertEqual(load_config().namespace, "process-namespace")
            with patch.dict(os.environ, {"PINECONE_HOST": "https://unrelated.example"}):
                with self.assertRaisesRegex(ValueError, "PINECONE_HOST"):
                    load_config()
            with self.assertRaises(FileNotFoundError):
                load_config(root / "missing.env")

    def test_batched_upsert_contract_and_namespace(self):
        self.write_records([make_record(index) for index in range(3)])
        with patch("ingestion.upload_to_pinecone.load_config", return_value=CONFIG), \
             patch("ingestion.upload_to_pinecone.pinecone_request", side_effect=[
                 INDEX, {"upsertedCount": 2}, {"upsertedCount": 1}
             ]) as request, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(upload_embeddings(self.source, batch_size=2), 3)
        self.assertEqual(request.call_args_list[0].args[0], "https://api.pinecone.io/indexes/rag-poc")
        first_batch = request.call_args_list[1].args
        self.assertEqual(first_batch[0], CONFIG.host + "/vectors/upsert")
        self.assertEqual(first_batch[2]["namespace"], "testdata")
        self.assertEqual([row["id"] for row in first_batch[2]["vectors"]], ["guide-0", "guide-1"])
        self.assertEqual(request.call_args_list[2].args[2]["vectors"][0]["id"], "guide-2")

    def test_preflight_failures_and_dry_run_do_not_upsert(self):
        self.write_records([make_record()])
        for index in ({**INDEX, "dimension": 768}, {**INDEX, "status": {"ready": False}},
                      {**INDEX, "host": "wrong.svc.pinecone.io"}, {**INDEX, "vector_type": "sparse"}):
            with self.subTest(index=index):
                with patch("ingestion.upload_to_pinecone.load_config", return_value=CONFIG), \
                     patch("ingestion.upload_to_pinecone.pinecone_request", return_value=index) as request:
                    with self.assertRaises(ValueError):
                        upload_embeddings(self.source)
                    self.assertEqual(request.call_count, 1)
        with patch("ingestion.upload_to_pinecone.load_config", return_value=CONFIG), \
             patch("ingestion.upload_to_pinecone.pinecone_request", return_value=INDEX) as request, \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(upload_embeddings(self.source, dry_run=True), 1)
            self.assertEqual(request.call_count, 1)

    def test_late_batch_failure_reports_acknowledged_progress(self):
        self.write_records([make_record(index) for index in range(3)])
        with patch("ingestion.upload_to_pinecone.load_config", return_value=CONFIG), \
             patch("ingestion.upload_to_pinecone.pinecone_request", side_effect=[
                 INDEX, {"upsertedCount": 2}, RuntimeError("service unavailable")
             ]), patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(RuntimeError, "2/3 acknowledged records"):
                upload_embeddings(self.source, batch_size=2)

    def test_rest_headers_and_error_redaction(self):
        with patch("ingestion.upload_to_pinecone.urlopen") as request:
            request.return_value.__enter__.return_value = io.BytesIO(b'{"upsertedCount":1}')
            result = pinecone_request(CONFIG.host + "/vectors/upsert", CONFIG.api_key,
                                      {"namespace": "testdata", "vectors": []})
            self.assertEqual(result["upsertedCount"], 1)
            sent = request.call_args.args[0]
            self.assertEqual(sent.get_method(), "POST")
            self.assertEqual(sent.get_header("Api-key"), CONFIG.api_key)
            self.assertEqual(sent.get_header("X-pinecone-api-version"), "2025-10")
            self.assertEqual(json.loads(sent.data)["namespace"], "testdata")
        error = HTTPError(CONFIG.host, 403, "Forbidden", {}, io.BytesIO(CONFIG.api_key.encode()))
        with patch("ingestion.upload_to_pinecone.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                pinecone_request(CONFIG.host, CONFIG.api_key)
        self.assertNotIn(CONFIG.api_key, str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
