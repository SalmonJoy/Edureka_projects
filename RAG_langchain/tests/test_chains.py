"""Exercise actual LangChain pipelines with fake embedding/cloud/database clients."""

from dataclasses import replace
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from chains import build_ingestion_chain, build_question_chain
from components.answer_generator import NO_CONTEXT_ANSWER, generate_answer
from components.embeddings import embed_documents, embed_question, prepare_question
from components.pdf_loader import load_pdf
from components.pinecone_store import connect_index, search_pinecone
from components.text_splitter import split_documents
from config import Settings, load_settings
from main import main, print_result


SETTINGS = Settings("test-pinecone-key", "rag-poc", "https://example.svc.pinecone.io", "test-cloud-key")


class FakeEmbeddings(Embeddings):
    def __init__(self):
        self.document_inputs = []
        self.question_inputs = []

    def embed_documents(self, texts):
        self.document_inputs.extend(texts)
        return [[0.1] * 768 for _ in texts]

    def embed_query(self, text):
        self.question_inputs.append(text)
        return [0.2] * 768


class FakeIndex:
    def __init__(self):
        self.uploads = []

    def upsert(self, *, vectors, namespace):
        self.uploads.append((vectors, namespace))
        return SimpleNamespace(upserted_count=len(vectors))


def page(text="Admission is in Peabody Hall on the first floor.", number=0):
    return Document(page_content=text, metadata={"source": "guide.pdf", "page": number})


class ChainTests(unittest.TestCase):
    def test_pdf_loader_keeps_page_metadata_and_rejects_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.pdf"
            path.write_bytes(b"fake PDF for mocked loader")
            with patch("components.pdf_loader.PyPDFLoader") as loader:
                loader.return_value.load.return_value = [page(number=2), page(text=" ", number=3)]
                documents = load_pdf(path)
                self.assertEqual(len(documents), 1)
                self.assertEqual(documents[0].metadata, {"source": "guide.pdf", "page": 2})
                loader.assert_called_once_with(str(path), mode="page")
                with self.assertRaises(FileNotFoundError):
                    load_pdf(path.parent / "missing.pdf")
                loader.return_value.load.return_value = [page(text=" ")]
                with self.assertRaisesRegex(ValueError, "No PDF text"):
                    load_pdf(path)

    def test_splitter_offsets_size_metadata_and_stable_ids(self):
        original = page("One two three four five six seven eight nine ten. " * 10)
        chunks = split_documents([original], chunk_size=70, chunk_overlap=15)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            start = chunk.metadata["start_index"]
            self.assertLessEqual(len(chunk.page_content), 70)
            self.assertEqual(original.page_content[start:start + len(chunk.page_content)], chunk.page_content)
            self.assertEqual(chunk.metadata["page"], 0)
            self.assertEqual(chunk.metadata["source"], "guide.pdf")
        again = split_documents([original], chunk_size=70, chunk_overlap=15)
        self.assertEqual([c.metadata["chunk_id"] for c in chunks], [c.metadata["chunk_id"] for c in again])
        self.assertEqual(len({c.metadata["chunk_id"] for c in chunks}), len(chunks))

    def test_embedding_prefixes_question_preservation_and_validation(self):
        model = FakeEmbeddings()
        documents = [page()]
        records = embed_documents(documents, model, SETTINGS)
        data = embed_question(prepare_question(" Where is admission? "), model, SETTINGS)
        self.assertEqual(model.document_inputs, ["search_document: " + documents[0].page_content])
        self.assertEqual(model.question_inputs, ["search_query: Where is admission?"])
        self.assertIs(records[0]["document"], documents[0])
        self.assertEqual(data["question"], "Where is admission?")
        self.assertEqual(len(data["embedding"]), 768)
        with self.assertRaises(ValueError):
            prepare_question(" ")
        with self.assertRaisesRegex(ValueError, "768"):
            embed_documents(documents, SimpleNamespace(embed_documents=lambda texts: [[0.1]]), SETTINGS)

    def test_full_ingestion_chain_uses_precomputed_vectors_once(self):
        model, index = FakeEmbeddings(), FakeIndex()
        with patch("chains.create_embeddings", return_value=model), \
             patch("chains.connect_index", return_value=index), \
             patch("chains.load_pdf", return_value=[page()]):
            count = build_ingestion_chain(SETTINGS).invoke("guide.pdf")
        self.assertEqual(count, 1)
        self.assertEqual(len(model.document_inputs), 1)
        stored, namespace = index.uploads[0]
        self.assertEqual(namespace, "testdata-langchain")
        self.assertEqual(stored[0]["values"], [0.1] * 768)
        self.assertEqual(stored[0]["metadata"]["content"], page().page_content)
        self.assertEqual(stored[0]["metadata"]["embedding_model"], "nomic-embed-text:latest")
        self.assertEqual(stored[0]["id"], stored[0]["metadata"]["chunk_id"])

    def test_search_raw_threshold_boundary_and_precomputed_query(self):
        from unittest.mock import Mock
        store = Mock()
        store.similarity_search_by_vector_with_score.return_value = [
            (page(), 0.8), (page(number=1), 0.6), (page(number=2), 0.59999),
        ]
        vector = [0.2] * 768
        result = search_pinecone({"question": "Question?", "embedding": vector}, store, SETTINGS)
        self.assertEqual([score for _, score in result["matches"]], [0.8, 0.6])
        store.similarity_search_by_vector_with_score.assert_called_once_with(
            vector, k=3, namespace="testdata-langchain",
        )
        self.assertEqual(result["question"], "Question?")

    def test_full_question_chain_preserves_matches_and_labels_context(self):
        from unittest.mock import Mock
        model = FakeEmbeddings()
        store = Mock()
        store.similarity_search_by_vector_with_score.return_value = [(page(number=1.0), 0.8)]
        inputs = []
        def answer(values):
            inputs.append(values)
            return "Admission is in Peabody Hall. [1]"
        with patch("chains.create_embeddings", return_value=model), \
             patch("chains.connect_index", return_value=FakeIndex()), \
             patch("chains.create_vector_store", return_value=store), \
             patch("components.answer_generator.create_answer_chain", return_value=RunnableLambda(answer)):
            result = build_question_chain(SETTINGS).invoke(" Where is admission? ")
        self.assertEqual(result["question"], "Where is admission?")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(model.question_inputs, ["search_query: Where is admission?"])
        self.assertIn("[1] Source: guide.pdf, page 2\n", inputs[0]["context"])
        self.assertIn(page().page_content, inputs[0]["context"])
        self.assertEqual(result["answer"], "Admission is in Peabody Hall. [1]")
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            print_result(result)
        self.assertLess(output.getvalue().index("Grounded answer:"), output.getvalue().index("Result 1"))
        self.assertIn("Page: 2", output.getvalue())
        self.assertNotIn("Page: 2.0", output.getvalue())
        self.assertIn("Similarity score: 0.8000", output.getvalue())

    def test_actual_prompt_model_parser_chain_and_final_answer_only(self):
        captured = []
        def fake_chat(prompt):
            captured.extend(prompt.to_messages())
            return AIMessage(content="The first floor of Peabody Hall. [1]",
                             additional_kwargs={"reasoning_content": "not part of answer"})
        with patch("components.answer_generator.ChatOllama", return_value=RunnableLambda(fake_chat)) as chat:
            result = generate_answer({"question": "Where?", "matches": [(page(), 0.8)]}, SETTINGS)
        self.assertIn("using only the supplied chunks", captured[0].content)
        self.assertIn("Question:\nWhere?", captured[1].content)
        self.assertIn("[1] Source: guide.pdf, page 1", captured[1].content)
        self.assertEqual(result["answer"], "The first floor of Peabody Hall. [1]")
        self.assertEqual(chat.call_args.kwargs["client_kwargs"]["headers"]["Authorization"], "Bearer test-cloud-key")

    def test_empty_retrieval_skips_cloud_and_cloud_key_is_only_needed_for_answers(self):
        with patch("components.answer_generator.create_answer_chain") as create:
            result = generate_answer({"question": "Unknown?", "matches": []}, replace(SETTINGS, cloud_api_key=""))
            self.assertEqual(result["answer"], NO_CONTEXT_ANSWER)
            create.assert_not_called()
        with self.assertRaisesRegex(ValueError, "OLLAMA_API_KEY"):
            generate_answer({"question": "Where?", "matches": [(page(), 0.8)]}, replace(SETTINGS, cloud_api_key=""))

    def test_configuration_namespace_isolation_and_file_first_cloud_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "RAG_langchain"
            root.mkdir()
            (root.parent / ".env").write_text(
                "PINECONE_API_KEY=file-key\nPINECONE_INDEX_NAME=rag-poc\n"
                "PINECONE_HOST=https://example.svc.pinecone.io\n"
                "PINECONE_NAMESPACE=testdata\nOLLAMA_API_KEY=file-cloud-key\n", encoding="utf-8",
            )
            with patch("config.PROJECT_ROOT", root), patch.dict(os.environ, {"OLLAMA_API_KEY": "other-key"}, clear=True):
                settings = load_settings()
                self.assertEqual(settings.namespace, "testdata-langchain")
                self.assertEqual(settings.cloud_api_key, "file-cloud-key")
                self.assertNotIn("file-key", repr(settings))
                (root / ".env").write_text("PINECONE_LANGCHAIN_NAMESPACE=custom-langchain\n", encoding="utf-8")
                self.assertEqual(load_settings().namespace, "custom-langchain")

    def test_index_preflight_prevents_wrong_dimension_or_host(self):
        description = SimpleNamespace(status=SimpleNamespace(ready=True), dimension=10,
                                      metric="cosine", vector_type="dense", host="example.svc.pinecone.io")
        with patch("components.pinecone_store.Pinecone") as pinecone:
            pinecone.return_value.describe_index.return_value = description
            with self.assertRaisesRegex(ValueError, "768"):
                connect_index(SETTINGS)
            pinecone.return_value.Index.assert_not_called()
            description.dimension = 768
            description.host = "wrong.svc.pinecone.io"
            with self.assertRaisesRegex(ValueError, "does not match"):
                connect_index(SETTINGS)

    def test_cli_reports_missing_file_blank_question_and_service_errors(self):
        for arguments in (["main.py", "ingest", "definitely-missing.pdf"], ["main.py", "ask", " "]):
            with patch("sys.argv", arguments), patch("sys.stderr", new_callable=io.StringIO) as errors:
                self.assertEqual(main(), 1)
                self.assertIn("RAG failed:", errors.getvalue())
        with patch("sys.argv", ["main.py", "ask", "Question?"]), \
             patch("main.load_settings", return_value=SETTINGS), \
             patch("main.build_question_chain", side_effect=RuntimeError("Service unavailable")), \
             patch("sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(main(), 1)
            self.assertIn("Service unavailable", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
