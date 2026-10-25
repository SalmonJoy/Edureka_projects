"""Local Nomic embeddings for documents and user questions."""

import math

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings

from config import Settings


def create_embeddings(settings: Settings) -> OllamaEmbeddings:
    """Create the LangChain adapter for the local Ollama embedding model."""
    return OllamaEmbeddings(
        model=settings.embedding_model, base_url=settings.ollama_url,
        client_kwargs={"timeout": 120},
    )


def validate_vector(vector: list[float], dimensions: int) -> None:
    """Check that a vector is suitable for the configured Pinecone index."""
    if (len(vector) != dimensions or not any(vector)
            or not all(type(value) in (int, float) and math.isfinite(value) for value in vector)):
        raise ValueError(f"Expected a nonzero, finite {dimensions}-dimensional embedding")


def embed_documents(
    documents: list[Document], model: Embeddings, settings: Settings
) -> list[dict]:
    """Return each Document alongside its already-computed document vector."""
    texts = ["search_document: " + document.page_content for document in documents]
    vectors = model.embed_documents(texts)
    records = []
    for document, vector in zip(documents, vectors, strict=True):
        validate_vector(vector, settings.embedding_dimensions)
        records.append({"document": document, "embedding": vector})
    return records


def prepare_question(question: str) -> dict:
    """Start the question chain with a validated question in a simple dictionary."""
    question = question.strip()
    if not question:
        raise ValueError("Please enter a nonempty question")
    return {"question": question}


def embed_question(data: dict, model: Embeddings, settings: Settings) -> dict:
    """Preserve the question while adding its Nomic query embedding."""
    vector = model.embed_query("search_query: " + data["question"])
    validate_vector(vector, settings.embedding_dimensions)
    return {**data, "embedding": vector}
