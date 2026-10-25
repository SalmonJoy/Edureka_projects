"""Read these two pipe expressions to follow the complete RAG flows."""

from langchain_core.runnables import RunnableLambda

from components.answer_generator import generate_answer
from components.embeddings import create_embeddings, embed_documents, embed_question, prepare_question
from components.pdf_loader import load_pdf
from components.pinecone_store import connect_index, create_vector_store, search_pinecone, upsert_vectors
from components.text_splitter import split_documents
from config import Settings


def build_ingestion_chain(settings: Settings):
    """PDF path -> page Documents -> chunks -> computed vectors -> uploaded count."""
    model = create_embeddings(settings)
    index = connect_index(settings)
    pdf_loader = RunnableLambda(load_pdf)
    text_splitter = RunnableLambda(
        lambda pages: split_documents(pages, settings.chunk_size, settings.chunk_overlap)
    )
    convert_documents_to_embeddings = RunnableLambda(
        lambda documents: embed_documents(documents, model, settings)
    )
    upsert_to_pinecone = RunnableLambda(lambda records: upsert_vectors(records, index, settings))

    ingestion_chain = (
        pdf_loader | text_splitter | convert_documents_to_embeddings | upsert_to_pinecone
    )
    return ingestion_chain


def build_question_chain(settings: Settings):
    """Question -> question/vector -> scored Documents -> answer and Documents."""
    model = create_embeddings(settings)
    index = connect_index(settings)
    store = create_vector_store(index, model, settings)
    user_input = RunnableLambda(prepare_question)
    convert_question_to_embedding = RunnableLambda(lambda data: embed_question(data, model, settings))
    pinecone_search = RunnableLambda(lambda data: search_pinecone(data, store, settings))
    answer_generator = RunnableLambda(lambda data: generate_answer(data, settings))

    question_chain = (
        user_input | convert_question_to_embedding | pinecone_search | answer_generator
    )
    return question_chain
