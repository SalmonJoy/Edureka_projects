# RAG learning PoC

Step 1 converts a local PDF to Markdown using [Docling](https://docling-project.github.io/docling/getting_started/quickstart/).
Step 2 splits that Markdown into sections with context and saves JSONL.
Step 3 embeds the chunks with local Ollama and saves vectors alongside the text.
Step 4 uploads those vectors with chunk text and metadata to Pinecone.
Step 5 retrieves Pinecone chunks with similarity at least 0.6 and prints a grounded GPT-OSS answer before them.

## Setup

Run these PowerShell commands from the project folder. The existing `.venv`
uses Python 3.11; activation is optional because commands use its Python directly.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Docling downloads its required models on first use, so the first conversion
needs an internet connection and can take longer. PDF processing runs locally
using Docling's standard settings.

## Convert a PDF

Convert the supplied sample:

```powershell
.\.venv\Scripts\python.exe ingestion\pdf_to_markdown.py
```

This reads `source\pdf\RAG demo.pdf` and writes `source\markdown\RAG demo.md`.
The output folder is created automatically.

Convert another PDF by passing its path (quote paths containing spaces):

```powershell
.\.venv\Scripts\python.exe ingestion\pdf_to_markdown.py "C:\Documents\another file.pdf"
```

Explicit relative input paths are relative to your current working directory.
The default sample and output folder are always relative to the project, so
they also work when the script is invoked by its full path from another folder.
Outputs are UTF-8 Markdown named after the PDF. Re-running replaces an existing
output with the same name, including when different PDFs share that name.
The script prints the saved path on success and exits with a nonzero code and
an error message if input validation, conversion, or saving fails.

## How the code works

`ingestion/pdf_to_markdown.py` contains a reusable
`convert_pdf_to_markdown(pdf_path: Path) -> Path` function:

1. Validate that the input exists and has a `.pdf` extension.
2. Use `DocumentConverter().convert(pdf_path)` to parse the PDF into a Docling document.
3. Call `result.document.export_to_markdown()` to obtain Markdown text.
4. Save the nonempty result in `source\markdown` and return the saved path.

The command-line entry point uses `argparse` to accept an optional PDF path;
omitting it selects the sample.

## Chunk Markdown by section

Run the chunker on the converted sample:

```powershell
.\.venv\Scripts\python.exe ingestion\markdown_to_chunks.py
```

This reads `source\markdown\RAG demo.md` and creates
`source\chunks\RAG demo.jsonl`. The output folder is created automatically.
Run it on another Markdown file with:

```powershell
.\.venv\Scripts\python.exe ingestion\markdown_to_chunks.py "C:\Documents\another file.md"
```

As with PDF conversion, the default input and output folder work independently
of the current working directory. Explicit relative input paths are relative
to the working directory. Re-running replaces the JSONL file with the same name.
Chunking runs locally, requires no model downloads or API key, and does not
re-run PDF conversion.

### Section and context rules

- [markdown-it-py](https://markdown-it-py.readthedocs.io/en/latest/using.html)
  identifies Markdown headings (`#` through `######`, plus underlined headings).
  Headings inside code blocks, blockquotes, and list items do not split sections.
- A section extends to the next heading of any level. Its paragraphs, lists,
  code, and tables stay together. There is no size limit or overlap.
- Parent headings are carried into each child's `heading_path`. Parent body
  text is not copied into child chunks. Same-level headings are siblings;
  the chunker follows the Markdown hierarchy without guessing a new one.
- A heading without body content is kept as hierarchy context but does not
  produce its own chunk. Text before the first heading becomes a `Preamble`
  chunk; a preamble or section containing only Docling image markers is skipped.
  A document with text and no headings becomes one chunk.
- The first heading supplies `document_title`; if there are no headings, the
  filename stem supplies it. This also handles Docling documents whose titles
  are written as `##` instead of `#`.
- Context is structural: `contextualized_content` prepends the document title,
  source filename, and heading path to the section's Markdown. It does not
  generate an LLM summary. Use this field as input to a future embedding step.

### JSONL records

Each line is an independent JSON object. Newlines inside strings are escaped,
so a record always occupies one physical line. Fields are:

| Field | Meaning |
| --- | --- |
| `chunk_id` | Filename stem plus a sequential number, such as `RAG demo-0001`; unique within this document. |
| `source` | Original Markdown filename. |
| `document_title` | First heading, or filename stem if there are no headings. |
| `section_title` | Current heading, or `Preamble` for leading text. |
| `heading_level` | Current heading level from 1 to 6; 0 for leading text. |
| `heading_path` | Parent headings followed by the current heading. |
| `content` | Original section Markdown, including its heading, with outer whitespace trimmed. |
| `contextualized_content` | Context prefix plus `content`, ready for embedding. |

The reusable functions are `chunk_markdown(markdown, source_name)`, which
returns a list of chunk dictionaries, and `chunk_markdown_file(markdown_path)`,
which writes JSONL and returns its path. Empty input, missing files, and
non-Markdown inputs produce a clear error and a nonzero CLI exit code.

Run the project checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Embed chunks with local Ollama

Make sure the Ollama desktop app is running and `nomic-embed-text` is installed.
If you need to download the model on another machine, run:

```powershell
ollama pull nomic-embed-text
```

Embed the existing sample chunks:

```powershell
.\.venv\Scripts\python.exe ingestion\embed_chunks.py
```

This reads `source\chunks\RAG demo.jsonl` and writes
`source\embeddings\RAG demo.jsonl`, creating the output folder automatically.
The embedding script uses Python's standard library, so no additional Python
package installation is needed. With the model installed, requests go to
the local Ollama server at `http://localhost:11434`; no API key is needed.

For another chunks file or a different server port:

```powershell
.\.venv\Scripts\python.exe ingestion\embed_chunks.py "C:\Documents\other chunks.jsonl"
.\.venv\Scripts\python.exe ingestion\embed_chunks.py --ollama-url http://localhost:11434 --batch-size 8 --timeout 120
```

Default paths are project-relative and work from any working directory.
Explicit relative input paths are relative to the current working directory.
Existing embeddings with the same filename are replaced only after every batch
succeeds and the complete JSONL has been written. Failed requests leave an
existing output intact. The original chunks file is not modified.

### Embedding input and output

The script calls [Ollama's `/api/embed` endpoint](https://docs.ollama.com/api/embed)
in batches of eight, using `nomic-embed-text:latest`. Each model input is:

```text
search_document: <the chunk's contextualized_content>
```

The document prefix follows [Nomic's retrieval instructions](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5).
For future retrieval, embed questions with the same model and the
`search_query: ` prefix, then compare query and document vectors.

Each output line preserves all original chunk fields, including `chunk_id`,
`content`, `contextualized_content`, and heading metadata, and adds:

| Field | Meaning |
| --- | --- |
| `embedding` | The chunk's 768 floating-point vector values. |
| `embedding_metadata.provider` | `ollama` |
| `embedding_metadata.model` | `nomic-embed-text:latest` |
| `embedding_metadata.dimensions` | `768` |
| `embedding_metadata.input_field` | `contextualized_content` |
| `embedding_metadata.prefix` | `search_document: `, including its trailing space. |

Output is UTF-8 JSONL in input order, with one embedding per chunk. The reusable
function `embed_chunks_file(chunks_path, *, ollama_url, batch_size, timeout)`
returns the saved path. `embed_texts(texts, *, ollama_url, timeout)` handles a
batch of document texts and returns their vectors in order.

The script validates input records, unique chunk IDs, vector counts, and
finite, nonzero 768-dimension vectors. Errors include affected batch IDs and
exit with a nonzero status. Ollama's `truncate` setting is `false`, so an input
that exceeds the model's context limit fails instead of silently losing text.
If that happens, split the affected section into smaller chunks and retry.

## Upload embeddings to Pinecone

The uploader reads the existing embeddings JSONL; it does not generate new
embeddings. It uses Pinecone's REST API and `python-dotenv`, which is included
in `requirements.txt`. The current setup reads the parent folder's `.env`.
Required settings are:

```dotenv
PINECONE_API_KEY=your-pinecone-api-key
PINECONE_INDEX_NAME=rag-poc
PINECONE_HOST=https://your-index-host.pinecone.io
PINECONE_NAMESPACE=testdata
```

Process environment variables take priority over the project's `.env`, which
takes priority over the parent folder's `.env`. To select a specific file,
use `--env-file`; process settings still take priority. Keep API keys private
and out of version control; the script does not print the key.

Upload the sample's vectors into the configured index and namespace:

```powershell
.\.venv\Scripts\python.exe ingestion\upload_to_pinecone.py
```

Validate the file and remote index without writing records:

```powershell
.\.venv\Scripts\python.exe ingestion\upload_to_pinecone.py --dry-run
```

Use another embeddings file or explicit configuration:

```powershell
.\.venv\Scripts\python.exe ingestion\upload_to_pinecone.py "C:\Documents\other embeddings.jsonl" --env-file "C:\Config\.env" --batch-size 50 --timeout 30
```

Default paths work from any working directory; explicit relative paths are
relative to the working directory. All rows and batch sizes are validated
before uploading. The target index must be ready, support dense vectors,
match the configured host, and have the same dimension as the vectors.
The script uses the existing index rather than creating or reconfiguring it.

### Pinecone record format

Each record is sent to [Pinecone's vector upsert endpoint](https://docs.pinecone.io/reference/api/2025-10/data-plane/upsert) as:

- `id`: the original `chunk_id`.
- `values`: the original `embedding` vector.
- `metadata`: all other original fields, including `chunk_id`, `source`,
  `document_title`, `section_title`, `heading_level`, `heading_path`, `content`,
  and `contextualized_content`.

Pinecone's [vector metadata format](https://docs.pinecone.io/guides/index-data/data-modeling)
supports flat values, so `embedding_metadata` is flattened into
`embedding_provider`, `embedding_model`, `embedding_dimensions`,
`embedding_input_field`, and `embedding_prefix`. Heading paths stay lists of
strings. Unsupported metadata types, duplicate IDs, invalid vectors, and
metadata larger than 40 KB are rejected before upload.

Uploads use batches of 50 by default, limited to 1,000 records and 2 MB per
request. Re-running replaces records with the same IDs in the same namespace,
so it does not create duplicates. It does not remove old IDs if the input file
later shrinks. The local input file is not modified.

There is no transaction spanning batches. If a request fails, earlier batches
may already be stored. The error reports acknowledged progress; retrying the
same file safely upserts the same IDs again. Newly uploaded records may take
a short time to appear in fetch results or index statistics.

The reusable function is
`upload_embeddings(embeddings_path, *, env_file=None, batch_size=50, timeout=30, dry_run=False)`.
It returns the uploaded record count (the validated count for a dry run).
CLI errors exit with a nonzero status.

## Search for similar chunks

`ingestion/search_chunks.py` is a self-contained learning example with four
steps: embed a question, search Pinecone, generate a grounded answer, and
print the answer followed by the retrieved chunks. Start Ollama and use the
Pinecone configuration described above. Also set the cloud API key in your
project or parent `.env`:

```dotenv
OLLAMA_API_KEY=your-ollama-cloud-api-key
```

Run it interactively:

```powershell
.\.venv\Scripts\python.exe ingestion\search_chunks.py
```

Or pass a question directly:

```powershell
.\.venv\Scripts\python.exe ingestion\search_chunks.py "Where is the Office of Undergraduate Admission?"
```

The script embeds `search_query: <question>` with `nomic-embed-text:latest` via
local Ollama, then sends the 768-dimensional vector to the configured Pinecone
host and namespace (`rag-poc` / `testdata` in the current setup). It requests
the top three matches and their stored metadata, then keeps only scores
greater than or equal to `SIMILARITY_THRESHOLD = 0.6`. Filtering uses the raw
score before rounding. The question and retained chunk text, with source and
section labels, are then sent to `gpt-oss:120b` at
`https://ollama.com/api/chat` using the cloud API key. The prompt instructs
the model to use only the chunks, cite them with labels such as `[1]`, and
state when they do not provide enough information. Only the final answer
text is printed; the model's thinking field is not displayed.

The answer appears first, followed by each remaining match's rank,
raw cosine similarity score to four decimal places, section, source filename,
and full chunk Markdown. Higher scores indicate greater similarity; they are
not confidence percentages. If fewer than three matches meet the threshold,
it prints those available. If none qualify, it skips the cloud call and
prints an insufficient-information answer plus a message mentioning the
threshold. Answer citations map to the printed results: `[1]` means Result 1.

`embed_question(question)` and
`search_similar_chunks(embedding, host, api_key, namespace)` show the two API
calls directly using `requests`. `generate_grounded_answer(question, matches,
api_key)` shows the cloud generation call. Pinecone settings follow process
environment, project `.env`, then parent `.env` precedence. The cloud key
specifically prefers the project `.env`, then parent `.env`, then the process
environment as a fallback, so an inherited key does not override your file's key.
Requests time out after 120
seconds for local embedding and cloud generation, and 30 seconds for Pinecone. Blank questions, missing
configuration, and failed HTTP requests produce an error and a nonzero exit.
The script prints the answer and chunks without saving files.
