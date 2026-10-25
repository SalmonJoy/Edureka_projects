"""Check section boundaries, heading context, and JSONL output."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ingestion.markdown_to_chunks import chunk_markdown, chunk_markdown_file


class MarkdownChunkerTests(unittest.TestCase):
    def test_heading_hierarchy_and_parent_body(self):
        chunks = chunk_markdown(
            "# Guide\nIntro\n\n## Install\nSetup\n\n### Windows\nSteps\n\n## Usage\nRun\n",
            "guide.md",
        )
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[2]["heading_path"], ["Guide", "Install", "Windows"])
        self.assertEqual(chunks[3]["heading_path"], ["Guide", "Usage"])
        self.assertIn("Guide > Install > Windows", chunks[2]["contextualized_content"])
        self.assertNotIn("Setup", chunks[2]["content"])
        self.assertEqual(chunks[0]["content"], "# Guide\nIntro")

    def test_code_tables_and_nested_block_headings_stay_in_section(self):
        content = (
            "# Guide\n\n```python\n# code comment\n```\n\n"
            "~~~text\n## fenced heading\n~~~\n\n"
            "    # indented code\n\n> ## Quoted heading\n\n"
            "- ## List heading\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        )
        chunks = chunk_markdown(content, "guide.md")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["content"], content.strip())

    def test_empty_headings_supply_context_without_empty_chunks(self):
        chunks = chunk_markdown(
            "<!-- image -->\n\n# Guide\n\n## Setup\n\n### Windows\nInstall here.\n",
            "guide.md",
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["heading_path"], ["Guide", "Setup", "Windows"])
        self.assertEqual(chunks[0]["document_title"], "Guide")

    def test_underlined_headings_and_duplicate_titles(self):
        chunks = chunk_markdown(
            "Guide\n=====\nIntro\n\nSetup\n-----\nFirst\n\nSetup\n-----\nSecond\n",
            "guide.md",
        )
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[1]["heading_path"], ["Guide", "Setup"])
        self.assertEqual(chunks[2]["heading_path"], ["Guide", "Setup"])
        self.assertEqual(len({chunk["chunk_id"] for chunk in chunks}), 3)

    def test_preamble_no_headings_and_line_endings(self):
        chunks = chunk_markdown("\ufeffLeading text\r\n\r\n## Section\r\nCafé\r\n", "guide.md")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["section_title"], "Preamble")
        self.assertEqual(chunks[0]["heading_level"], 0)
        self.assertEqual(chunks[0]["content"], "Leading text")
        self.assertIn("Café", chunks[1]["content"])
        plain = chunk_markdown("Plain text\n\nMore text", "notes.md")
        self.assertEqual(len(plain), 1)
        self.assertEqual(plain[0]["document_title"], "notes")

    def test_empty_input(self):
        for text in ("", " \n", "<!-- image -->\n", "# Title\n"):
            with self.subTest(text=text):
                self.assertEqual(chunk_markdown(text, "guide.md"), [])

    def test_jsonl_unicode_and_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            markdown = root / "sample file.md"
            output_dir = root / "chunks"
            markdown.write_text("# Guide\nCafé\n\n## Next\nMore\n", encoding="utf-8")
            with patch("ingestion.markdown_to_chunks.CHUNKS_DIR", output_dir):
                output = chunk_markdown_file(markdown)
                self.assertEqual(output, output_dir / "sample file.jsonl")
                records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(records), 2)
                self.assertIn("Café", records[0]["content"])
                markdown.write_text("# Guide\nReplaced\n", encoding="utf-8")
                chunk_markdown_file(markdown)
                records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(records), 1)
                self.assertIn("Replaced", records[0]["content"])

    def test_invalid_file_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(FileNotFoundError):
                chunk_markdown_file(root / "missing.md")
            other = root / "wrong.txt"
            other.write_text("text", encoding="utf-8")
            with self.assertRaises(ValueError):
                chunk_markdown_file(other)
            empty = root / "empty.md"
            empty.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                chunk_markdown_file(empty)


if __name__ == "__main__":
    unittest.main()
