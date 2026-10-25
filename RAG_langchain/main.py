"""Run either learning chain: ingest a PDF, or ask a question."""

import argparse
from pathlib import Path
import sys

from chains import build_ingestion_chain, build_question_chain
from config import DEFAULT_PDF, load_settings


def print_result(result: dict) -> None:
    """Print the grounded answer before its numbered supporting chunks."""
    print(f"\nGrounded answer:\n{result['answer']}")
    if not result["matches"]:
        print("No chunks met the similarity threshold.")
    for rank, (document, score) in enumerate(result["matches"], start=1):
        print(f"\nResult {rank} | Similarity score: {score:.4f}")
        print(f"Source: {document.metadata['source']} | Page: {int(document.metadata['page']) + 1}")
        print(document.page_content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Load, split, embed, and upsert a PDF")
    ingest.add_argument("pdf_path", type=Path, nargs="?", default=DEFAULT_PDF)
    ask = commands.add_parser("ask", help="Retrieve chunks and generate a grounded answer")
    ask.add_argument("question", nargs="?", help="Prompts interactively when omitted")
    args = parser.parse_args()
    try:
        if args.command == "ingest":
            path = args.pdf_path.expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"PDF file not found: {path}")
            settings = load_settings()
            count = build_ingestion_chain(settings).invoke(path)
            print(f"Upserted {count} chunks into {settings.pinecone_index_name}/{settings.namespace}")
        else:
            question = args.question if args.question is not None else input("Ask a question: ")
            if not question.strip():
                raise ValueError("Please enter a nonempty question")
            settings = load_settings()
            result = build_question_chain(settings).invoke(question)
            print_result(result)
    except Exception as exc:
        print(f"RAG failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
