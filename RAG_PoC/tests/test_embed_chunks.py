"""Check embedding API contracts, input validation, and saved record mapping."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from ingestion.embed_chunks import DIMENSIONS, DOCUMENT_PREFIX, MODEL, embed_chunks_file, embed_texts


def make_chunk(index):
    return {
        "chunk_id": f"sample-{index}",
        "source": "sample.md",
        "content": f"## Section {index}\nCafé {index}",
        "contextualized_content": f"Document: Guide\nSection: {index}\nCafé {index}",
        "heading_path": ["Guide", f"Section {index}"],
        "custom_metadata": {"page": index},
    }


def vector(value=0.1):
    return [value] * DIMENSIONS


class EmbedChunksTests(unittest.TestCase):
    def test_api_payload_prefix_and_order(self):
        expected = [vector(0.1), vector(0.2)]
        body = io.BytesIO(json.dumps({"embeddings": expected}).encode())
        with patch("ingestion.embed_chunks.urlopen") as request:
            request.return_value.__enter__.return_value = body
            self.assertEqual(embed_texts(["Café", "Second"], timeout=15), expected)
        args, kwargs = request.call_args
        self.assertEqual(args[0].full_url, "http://localhost:11434/api/embed")
        self.assertEqual(json.loads(args[0].data), {
            "model": MODEL,
            "input": [DOCUMENT_PREFIX + "Café", DOCUMENT_PREFIX + "Second"],
            "truncate": False,
        })
        self.assertEqual(kwargs["timeout"], 15)

    def test_invalid_api_vectors(self):
        cases = [[], [[1, 2]], [vector(float("nan"))], [vector(float("inf"))],
                 [vector(True)], [vector(0)], [vector("bad")]]
        for vectors in cases:
            with self.subTest(vectors=str(vectors)[:30]):
                with patch("ingestion.embed_chunks.urlopen") as request:
                    request.return_value.__enter__.return_value = io.BytesIO(
                        json.dumps({"embeddings": vectors}).encode()
                    )
                    with self.assertRaises(RuntimeError):
                        embed_texts(["test"])

    def test_api_http_connection_and_bad_json_errors(self):
        http_error = HTTPError("http://localhost:11434/api/embed", 400, "Bad Request", {},
                               io.BytesIO(b'{"error":"input exceeds context length"}'))
        for error, message in ((http_error, "context length"),
                               (URLError("connection refused"), "Check that Ollama is running"),
                               (TimeoutError("timed out"), "increase --timeout")):
            with self.subTest(error=error):
                with patch("ingestion.embed_chunks.urlopen", side_effect=error):
                    with self.assertRaisesRegex(RuntimeError, message):
                        embed_texts(["test"])
        with patch("ingestion.embed_chunks.urlopen") as request:
            request.return_value.__enter__.return_value = io.BytesIO(b'not JSON')
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                embed_texts(["test"])

    def test_file_batches_preserve_text_metadata_and_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sample file.jsonl"
            chunks = [make_chunk(index) for index in range(3)]
            original = "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n"
            source.write_text(original, encoding="utf-8")
            with patch("ingestion.embed_chunks.EMBEDDINGS_DIR", root / "embeddings"), \
                 patch("ingestion.embed_chunks.embed_texts", side_effect=[
                     [vector(0.1), vector(0.2)], [vector(0.3)]
                 ]) as embed, patch("sys.stdout", new_callable=io.StringIO):
                output = embed_chunks_file(source, batch_size=2)
            self.assertEqual(output, root / "embeddings" / "sample file.jsonl")
            saved = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(saved), 3)
            self.assertEqual(embed.call_args_list[0].args[0], [c["contextualized_content"] for c in chunks[:2]])
            self.assertEqual(embed.call_args_list[1].args[0], [chunks[2]["contextualized_content"]])
            for index, record in enumerate(saved):
                self.assertEqual({key: record[key] for key in chunks[index]}, chunks[index])
                self.assertEqual(record["embedding"], vector((index + 1) / 10))
                self.assertEqual(record["embedding_metadata"]["model"], MODEL)
                self.assertEqual(record["embedding_metadata"]["prefix"], DOCUMENT_PREFIX)
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_late_api_failure_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sample.jsonl"
            source.write_text("\n".join(json.dumps(make_chunk(i)) for i in range(2)), encoding="utf-8")
            destination = root / "embeddings"
            destination.mkdir()
            output = destination / source.name
            output.write_text("previous complete output\n", encoding="utf-8")
            with patch("ingestion.embed_chunks.EMBEDDINGS_DIR", destination), \
                 patch("ingestion.embed_chunks.embed_texts", side_effect=[[vector()], RuntimeError("offline")]), \
                 patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaisesRegex(RuntimeError, "sample-1.*offline"):
                    embed_chunks_file(source, batch_size=1)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous complete output\n")
            self.assertEqual(list(destination.glob("*.tmp")), [])

    def test_invalid_inputs_fail_before_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.jsonl"
            cases = ["", "not JSON", "[]", json.dumps({"chunk_id": "missing-content"}),
                     json.dumps({**make_chunk(0), "contextualized_content": " "}),
                     "\n".join([json.dumps(make_chunk(0))] * 2)]
            with patch("ingestion.embed_chunks.embed_texts") as embed:
                for text in cases:
                    with self.subTest(text=text[:40]):
                        source.write_text(text, encoding="utf-8")
                        with self.assertRaises(ValueError):
                            embed_chunks_file(source)
                with self.assertRaises(FileNotFoundError):
                    embed_chunks_file(Path(temp_dir) / "missing.jsonl")
                wrong = Path(temp_dir) / "wrong.txt"
                wrong.write_text("text", encoding="utf-8")
                with self.assertRaises(ValueError):
                    embed_chunks_file(wrong)
                for options in ({"batch_size": 0}, {"timeout": 0}, {"timeout": float("nan")}):
                    with self.assertRaises(ValueError):
                        embed_chunks_file(source, **options)
                embed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
