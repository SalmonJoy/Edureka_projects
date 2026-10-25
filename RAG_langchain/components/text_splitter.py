"""Page Documents -> smaller Documents with overlap and stable IDs."""

import hashlib

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_documents(
    documents: list[Document], chunk_size: int = 1000, chunk_overlap: int = 200
) -> list[Document]:
    """Split within pages and preserve source, page, and character start offset."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap, add_start_index=True,
    )
    chunks = splitter.split_documents(documents)
    for position, chunk in enumerate(chunks, start=1):
        identity = (f"{chunk.metadata['source']}|{chunk.metadata['page']}|"
                    f"{chunk.metadata['start_index']}|{chunk.page_content}")
        chunk.metadata["chunk_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        chunk.metadata["chunk_index"] = position
    if not chunks:
        raise ValueError("No text chunks were produced")
    return chunks
