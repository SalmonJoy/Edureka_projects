"""Check query embeddings, Pinecone search, and readable console output."""

import io
import os
import unittest
from unittest.mock import patch

import requests

from ingestion.search_chunks import (
    NO_CONTEXT_ANSWER, embed_question, generate_grounded_answer, main, search_similar_chunks,
)


SETTINGS = {"PINECONE_API_KEY": "test-key", "PINECONE_HOST": "https://example.svc.pinecone.io",
            "PINECONE_NAMESPACE": "testdata", "OLLAMA_API_KEY": "test-cloud-key"}


class SearchChunksTests(unittest.TestCase):
    def test_question_embedding_uses_query_prefix(self):
        embedding = [0.1] * 768
        with patch("ingestion.search_chunks.requests.post") as post:
            post.return_value.json.return_value = {"embeddings": [embedding]}
            self.assertEqual(embed_question("Where is admission?"), embedding)
        self.assertEqual(post.call_args.args[0], "http://localhost:11434/api/embed")
        self.assertEqual(post.call_args.kwargs["json"], {
            "model": "nomic-embed-text:latest", "input": "search_query: Where is admission?",
            "truncate": False,
        })
        self.assertEqual(post.call_args.kwargs["timeout"], 120)
        post.return_value.raise_for_status.assert_called_once()

    def test_pinecone_request_asks_for_three_chunks_and_metadata(self):
        with patch("ingestion.search_chunks.requests.post") as post:
            post.return_value.json.return_value = {"matches": [
                {"id": "guide-1", "score": 0.9},
                {"id": "guide-2", "score": 0.6},
                {"id": "guide-3", "score": 0.59999},
            ]}
            matches = search_similar_chunks([0.1] * 768, SETTINGS["PINECONE_HOST"] + "/",
                                             SETTINGS["PINECONE_API_KEY"], "testdata")
        self.assertEqual([match["id"] for match in matches], ["guide-1", "guide-2"])
        self.assertEqual(post.call_args.args[0], SETTINGS["PINECONE_HOST"] + "/query")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["namespace"], "testdata")
        self.assertEqual(payload["topK"], 3)
        self.assertTrue(payload["includeMetadata"])
        self.assertFalse(payload["includeValues"])
        self.assertEqual(len(payload["vector"]), 768)

    def test_interactive_question_and_full_content_with_fewer_results(self):
        content = "## Admission\n\nVisit Peabody Hall.\nMore information."
        matches = [{"score": 0.912345, "metadata": {
            "section_title": "Admission", "source": "guide.md", "content": content,
        }}]
        with patch.dict(os.environ, SETTINGS, clear=True), \
             patch("ingestion.search_chunks.load_dotenv"), \
             patch("sys.argv", ["search_chunks.py"]), \
             patch("builtins.input", return_value="  Where is admission?  "), \
             patch("ingestion.search_chunks.dotenv_values", return_value={"OLLAMA_API_KEY": "env-cloud-key"}), \
             patch("ingestion.search_chunks.embed_question", return_value=[0.1] * 768) as embed, \
             patch("ingestion.search_chunks.search_similar_chunks", return_value=matches), \
             patch("ingestion.search_chunks.generate_grounded_answer", return_value="Visit Peabody Hall. [1]") as generate, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(), 0)
        embed.assert_called_once_with("Where is admission?")
        generate.assert_called_once_with("Where is admission?", matches, "env-cloud-key")
        self.assertIn("Grounded answer:\nVisit Peabody Hall. [1]", output.getvalue())
        self.assertLess(output.getvalue().index("Grounded answer:"), output.getvalue().index("Result 1"))
        self.assertIn("Result 1 | Similarity score: 0.9123", output.getvalue())
        self.assertIn("Section: Admission", output.getvalue())
        self.assertIn("Source: guide.md", output.getvalue())
        self.assertIn(content, output.getvalue())
        self.assertNotIn("Result 2", output.getvalue())

    def test_empty_question_does_not_call_services(self):
        with patch("sys.argv", ["search_chunks.py", "  "]), \
             patch("ingestion.search_chunks.requests.post") as post, \
             patch("sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(main(), 1)
        post.assert_not_called()
        self.assertIn("nonempty question", errors.getvalue())

    def test_no_matches_message(self):
        with patch.dict(os.environ, SETTINGS, clear=True), \
             patch("ingestion.search_chunks.load_dotenv"), \
             patch("ingestion.search_chunks.dotenv_values", return_value={}), \
             patch("sys.argv", ["search_chunks.py", "A question"]), \
             patch("ingestion.search_chunks.embed_question", return_value=[0.1] * 768), \
             patch("ingestion.search_chunks.search_similar_chunks", return_value=[]), \
             patch("ingestion.search_chunks.requests.post") as post, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(), 0)
        post.assert_not_called()
        self.assertIn(NO_CONTEXT_ANSWER, output.getvalue())
        self.assertIn("No chunks found with a similarity score of at least 0.6.", output.getvalue())

    def test_grounded_answer_sends_question_and_labeled_chunks(self):
        matches = [{"metadata": {"content": "Admission is in Peabody Hall.",
                                  "section_title": "Admission", "source": "guide.md"}}]
        with patch("ingestion.search_chunks.requests.post") as post:
            post.return_value.json.return_value = {"message": {
                "content": "Admission is in Peabody Hall. [1]", "thinking": "Not printed",
            }}
            answer = generate_grounded_answer("Where is admission?", matches, "test-cloud-key")
        self.assertEqual(answer, "Admission is in Peabody Hall. [1]")
        self.assertEqual(post.call_args.args[0], "https://ollama.com/api/chat")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-cloud-key")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "gpt-oss:120b")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("using only the retrieved chunks", payload["messages"][0]["content"])
        prompt = payload["messages"][1]["content"]
        self.assertIn("Where is admission?", prompt)
        self.assertIn("[1] Source: guide.md", prompt)
        self.assertIn("Admission is in Peabody Hall.", prompt)
        post.return_value.raise_for_status.assert_called_once()

    def test_generation_without_chunk_text_skips_cloud(self):
        for matches in ([], [{"metadata": {"content": " "}}]):
            with self.subTest(matches=matches), patch("ingestion.search_chunks.requests.post") as post:
                self.assertEqual(generate_grounded_answer("Question?", matches, ""), NO_CONTEXT_ANSWER)
                post.assert_not_called()

    def test_missing_cloud_key_and_empty_answer(self):
        matches = [{"metadata": {"content": "Some reference text."}}]
        with patch("ingestion.search_chunks.requests.post") as post:
            with self.assertRaisesRegex(ValueError, "OLLAMA_API_KEY"):
                generate_grounded_answer("Question?", matches, "")
            post.assert_not_called()
            post.return_value.json.return_value = {"message": {"content": " "}}
            with self.assertRaisesRegex(ValueError, "empty answer"):
                generate_grounded_answer("Question?", matches, "test-cloud-key")

    def test_missing_configuration_and_connection_errors(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("ingestion.search_chunks.load_dotenv"), \
             patch("sys.argv", ["search_chunks.py", "A question"]), \
             patch("ingestion.search_chunks.requests.post") as post, \
             patch("sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(main(), 1)
            post.assert_not_called()
            self.assertIn("PINECONE_API_KEY", errors.getvalue())
        with patch.dict(os.environ, SETTINGS, clear=True), \
             patch("ingestion.search_chunks.load_dotenv"), \
             patch("sys.argv", ["search_chunks.py", "A question"]), \
             patch("ingestion.search_chunks.requests.post", side_effect=requests.ConnectionError("Ollama unavailable")), \
             patch("sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(main(), 1)
            self.assertIn("Ollama unavailable", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
