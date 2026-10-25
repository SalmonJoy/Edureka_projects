"""PDF path -> a list of page Documents."""

from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document


def load_pdf(pdf_path: str | Path) -> list[Document]:
    """Read PDF text one page at a time, preserving its source and page number."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF file not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {path}")
    pages = PyPDFLoader(str(path), mode="page").load()
    documents = [
        Document(page_content=page.page_content, metadata={
            "source": path.name,
            "page": page.metadata["page"],  # PyPDF uses zero-based page numbers.
        })
        for page in pages if page.page_content.strip()
    ]
    if not documents:
        raise ValueError("No PDF text found. This example needs a PDF with extractable text.")
    return documents
