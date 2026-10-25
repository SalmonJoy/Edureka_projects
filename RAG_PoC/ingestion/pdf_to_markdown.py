"""Convert a local PDF into Markdown using Docling."""

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = PROJECT_ROOT / "source" / "pdf" / "RAG demo.pdf"
MARKDOWN_DIR = PROJECT_ROOT / "source" / "markdown"


def convert_pdf_to_markdown(pdf_path: Path) -> Path:
    """Convert a PDF and return its saved Markdown path.

    Output always goes to the project's source/markdown folder. An existing
    Markdown file with the same filename stem is replaced after conversion.
    """
    pdf_path = Path(pdf_path).expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {pdf_path}")

    # Import after validation so invalid paths fail without loading Docling.
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    markdown = result.document.export_to_markdown()
    if not markdown.strip():
        raise ValueError(f"No Markdown content was extracted from: {pdf_path}")

    MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    output_path = MARKDOWN_DIR / f"{pdf_path.stem}.md"
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def main() -> int:
    """Parse an optional PDF path and run the conversion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pdf_path",
        nargs="?",
        type=Path,
        default=DEFAULT_PDF,
        help="PDF to convert (default: source/pdf/RAG demo.pdf in this project)",
    )
    args = parser.parse_args()

    try:
        output_path = convert_pdf_to_markdown(args.pdf_path)
    except Exception as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1

    print(f"Markdown saved to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
