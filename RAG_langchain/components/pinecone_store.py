"""Pinecone connection, precomputed-vector upsert, and scored retrieval."""

from urllib.parse import urlparse

from langchain_core.embeddings import Embeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone

from config import Settings


def connect_index(settings: Settings):
    """Validate the existing dense cosine index before either chain uses it."""
    client = Pinecone(api_key=settings.pinecone_api_key)
    description = client.describe_index(settings.pinecone_index_name)
    if not description.status.ready:
        raise ValueError("The Pinecone index is not ready")
    if description.dimension != settings.embedding_dimensions:
        raise ValueError(f"Pinecone index must have {settings.embedding_dimensions} dimensions")
    if description.metric != "cosine" or description.vector_type != "dense":
        raise ValueError("This example expects a dense index with cosine similarity")
    if description.host != urlparse(settings.pinecone_host).hostname:
        raise ValueError("PINECONE_HOST does not match PINECONE_INDEX_NAME")
    return client.Index(host=settings.pinecone_host, timeout=30)


def upsert_vectors(records: list[dict], index, settings: Settings) -> int:
    """Store precomputed vectors and flat metadata without embedding them again."""
    vectors = []
    for record in records:
        document = record["document"]
        vectors.append({
            "id": document.metadata["chunk_id"],
            "values": record["embedding"],
            "metadata": {
                **document.metadata, "content": document.page_content,
                "embedding_model": settings.embedding_model,
                "embedding_dimensions": settings.embedding_dimensions,
                "embedding_prefix": "search_document: ",
            },
        })
    uploaded = 0
    for start in range(0, len(vectors), 50):
        batch = vectors[start:start + 50]
        response = index.upsert(vectors=batch, namespace=settings.namespace)
        if response.upserted_count != len(batch):
            raise RuntimeError("Pinecone returned an unexpected upserted count")
        uploaded += response.upserted_count
    return uploaded


def create_vector_store(index, model: Embeddings, settings: Settings) -> PineconeVectorStore:
    """Tell LangChain which metadata field contains the stored chunk text."""
    return PineconeVectorStore(
        index=index, embedding=model, text_key="content", namespace=settings.namespace,
    )


def search_pinecone(data: dict, store: PineconeVectorStore, settings: Settings) -> dict:
    """Retrieve using the supplied vector, then filter the raw cosine scores."""
    matches = store.similarity_search_by_vector_with_score(
        data["embedding"], k=settings.top_k, namespace=settings.namespace,
    )
    retained = [(document, score) for document, score in matches
                if score >= settings.similarity_threshold]
    return {"question": data["question"], "matches": retained}
