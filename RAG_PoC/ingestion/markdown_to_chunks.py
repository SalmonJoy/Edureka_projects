"""Split Markdown into sections with heading context and save JSONL."""

import argparse
import json
from pathlib import Path
import sys

from markdown_it import MarkdownIt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKDOWN = PROJECT_ROOT / "source" / "markdown" / "RAG demo.md"
CHUNKS_DIR = PROJECT_ROOT / "source" / "chunks"


def chunk_markdown(markdown: str, source_name: str) -> list[dict]:
    """Return one contextual chunk per section that has body content.

    Heading hierarchy provides context without an LLM. Sections end at the next
    heading, including a child heading; parent text is not duplicated. Code,
    lists, and tables stay inside their section, with no fixed size splitting.
    """
    # Normalize line endings before using the parser's zero-based line ranges.
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = markdown.splitlines(keepends=True)
    tokens = MarkdownIt("commonmark").enable("table").parse(markdown)
    headings = []
    for index, token in enumerate(tokens):
        # Ignore headings embedded in blockquotes or list items.
        if token.type == "heading_open" and token.level == 0:
            headings.append(
                (token.map[0], token.map[1], int(token.tag[1:]), tokens[index + 1].content)
            )

    document_title = headings[0][3] if headings else Path(source_name).stem
    chunks = []
    heading_stack = []

    def add_chunk(start: int, body_start: int, end: int) -> None:
        body = "".join(lines[body_start:end])
        # Docling can emit image markers before the first heading. Avoid making
        # an empty retrieval chunk from those markers or a heading alone.
        if not body.replace("<!-- image -->", "").strip():
            return

        content = "".join(lines[start:end]).strip()
        heading_path = [title for _, title in heading_stack]
        section_title = heading_path[-1] if heading_path else "Preamble"
        section_context = " > ".join(heading_path) if heading_path else "Preamble"
        chunks.append(
            {
                "chunk_id": f"{Path(source_name).stem}-{len(chunks) + 1:04d}",
                "source": source_name,
                "document_title": document_title,
                "section_title": section_title,
                "heading_level": heading_stack[-1][0] if heading_stack else 0,
                "heading_path": heading_path,
                "content": content,
                "contextualized_content": (
                    f"Document: {document_title}\n"
                    f"Source: {source_name}\n"
                    f"Section: {section_context}\n\n{content}"
                ),
            }
        )

    first_heading = headings[0][0] if headings else len(lines)
    add_chunk(0, 0, first_heading)
    for index, (start, body_start, level, title) in enumerate(headings):
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, title))
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        add_chunk(start, body_start, end)

    return chunks


def chunk_markdown_file(markdown_path: Path) -> Path:
    """Write UTF-8 JSONL in source/chunks and return the saved path."""
    markdown_path = Path(markdown_path).expanduser().resolve()
    if not markdown_path.is_file():
        raise FileNotFoundError(f"Markdown file not found: {markdown_path}")
    if markdown_path.suffix.lower() != ".md":
        raise ValueError(f"Expected a .md file: {markdown_path}")

    chunks = chunk_markdown(markdown_path.read_text(encoding="utf-8"), markdown_path.name)
    if not chunks:
        raise ValueError(f"No section content found in: {markdown_path}")

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CHUNKS_DIR / f"{markdown_path.stem}.jsonl"
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for chunk in chunks:
            output.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return output_path


def main() -> int:
    """Accept an optional Markdown path; default to the converted sample."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "markdown_path",
        nargs="?",
        type=Path,
        default=DEFAULT_MARKDOWN,
        help="Markdown to chunk (default: source/markdown/RAG demo.md in this project)",
    )
    args = parser.parse_args()

    try:
        output_path = chunk_markdown_file(args.markdown_path)
    except Exception as exc:
        print(f"Chunking failed: {exc}", file=sys.stderr)
        return 1

    print(f"Chunks saved to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
