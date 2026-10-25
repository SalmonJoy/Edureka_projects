# Modular LangChain RAG

This learning example has two LangChain Expression Language (LCEL) chains.
Read `chains.py` first: it shows each component joined with `|`.

```text
PDF -> page Documents -> overlapping chunks -> local Nomic vectors -> Pinecone
Question -> local Nomic vector -> scored Pinecone chunks -> grounded cloud answer
```

## Setup

Use Python 3.11 or newer and a dedicated environment. From this folder, you
can use the existing PoC's interpreter to create one if needed:

```powershell
..\RAG_PoC\.venv\Scripts\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Keep the Ollama desktop app running with `nomic-embed-text` installed:

```powershell
ollama pull nomic-embed-text
```

The code reads shared credentials from `../.env`, with optional overrides in
this folder's `.env`. `.env.example` shows the required names. The existing
Pinecone index is `rag-poc`, with 768 dimensions and cosine similarity.

The project uses namespace **`testdata-langchain`**. It intentionally does not
read `PINECONE_NAMESPACE`, because the older PoC uses that for `testdata`.
Change `PINECONE_LANGCHAIN_NAMESPACE` if you want another namespace.

Pinecone configuration uses process environment values first, then project
`.env`, then parent `.env`. `OLLAMA_API_KEY` specifically prefers the project
file, then the parent file, then the process environment. This prevents the
different inherited cloud key on this machine from overriding the working file key.
The cloud key is needed only when generating an answer with retrieved context.
Keys are not printed or committed.

## Run the ingestion chain

```powershell
.\.venv\Scripts\python.exe main.py ingest
```

The default PDF is `../RAG_PoC/source/pdf/RAG demo.pdf`. You can pass any other
PDF with extractable text:

```powershell
.\.venv\Scripts\python.exe main.py ingest "C:\Documents\another file.pdf"
```

The chain returns the uploaded chunk count. Rerunning the same document uses
the same chunk IDs and updates those records. IDs are derived from filename,
page, character offset, and chunk text. Editing a document can introduce new
IDs; this example does not automatically delete older chunks. Uploads use
batches of 50 and are not a transaction across batches; after a failure, rerun
the same file to upsert the same IDs again.

## Run the question chain

```powershell
.\.venv\Scripts\python.exe main.py ask "Where is the Office of Undergraduate Admission?"
```

Or enter your question interactively:

```powershell
.\.venv\Scripts\python.exe main.py ask
```

The output prints the grounded answer first, followed by up to three chunks
with raw cosine similarity **at least 0.6**, their source filename, and a
one-based page number. Scores are not confidence percentages. Answer labels
such as `[1]` refer to the corresponding printed result.

The prompt asks GPT-OSS to answer using only the supplied chunks and to say
when the text is insufficient. If no chunks qualify, the code returns an
insufficient-information message without calling the cloud model. Model
thinking is kept separate from final answer text.

## Components to study

| Module | Input -> output | What to learn |
| --- | --- | --- |
| `components/pdf_loader.py` | PDF path -> `list[Document]` | `PyPDFLoader`, one Document per page |
| `components/text_splitter.py` | page Documents -> chunk Documents | `RecursiveCharacterTextSplitter`, metadata preservation |
| `components/embeddings.py` | Documents -> document/vector pairs; question dictionary -> question/vector dictionary | `OllamaEmbeddings`, Nomic task prefixes |
| `components/pinecone_store.py` | precomputed records -> uploaded count; question/vector -> scored Documents | SDK upsert, LangChain vector search, raw scores |
| `components/answer_generator.py` | question/scored Documents -> answer/scored Documents | `ChatPromptTemplate \| ChatOllama \| StrOutputParser` |
| `chains.py` | component functions -> runnable pipelines | `RunnableLambda`, `\|`, `.invoke()` |
| `config.py` | configuration -> `Settings` | shared settings and private credentials |
| `main.py` | CLI input -> printed results | invoking either chain |

The ingestion chain is:

```python
ingestion_chain = (
    pdf_loader | text_splitter | convert_documents_to_embeddings | upsert_to_pinecone
)
```

The question chain is:

```python
question_chain = (
    user_input | convert_question_to_embedding | pinecone_search | answer_generator
)
```

Stages are small functions wrapped with `RunnableLambda`. The question stages
carry a plain dictionary so the question is available when generating the
answer. Documents remain LangChain `Document` objects throughout the flow.
Stage outputs stay in memory; this example does not write Markdown or JSONL.

## Defaults and important details

- Chunks contain up to 1,000 characters, with a target overlap of 200, split
  within individual pages. Page metadata remains zero-based internally and is
  displayed as one-based. The splitter also records the character start offset.
- Both chains use local `nomic-embed-text:latest`, with `search_document: ` for
  documents and `search_query: ` for questions. The expected vector length is 768.
- The ingestion upsert uses **already-computed** vectors. Calling a vector
  store's `add_documents()` there would embed a second time, so the explicit
  embedding stage is followed by a Pinecone SDK upsert.
- Retrieval uses `PineconeVectorStore.similarity_search_by_vector_with_score()`
  with `k=3`, then filters raw scores before rounding. It does not re-embed the question.
- Chunk text is stored in Pinecone metadata under `content`. The vector store
  uses `text_key="content"` to reconstruct Documents. Source, page, chunk ID,
  offset, and embedding model information are stored as flat metadata.
- `ChatOllama` connects to `https://ollama.com` using `gpt-oss:120b` and the
  API key. Only the question and retrieved text with source/page labels go into
  the generation prompt. Embeddings stay local; generation runs in the cloud.
- Index readiness, host, dimensions, and cosine/dense configuration are checked
  before a chain uses Pinecone. The original `testdata` namespace is unaffected.
- Blank questions, missing files/configuration, and service failures print
  clear errors and exit with a nonzero status.

Settings such as chunk size, overlap, top-k, and threshold are visible in
`config.py`, so you can change one value and observe the effect.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests run real LangChain pipeline composition with fake service clients, so
they do not upload data or require API keys.
