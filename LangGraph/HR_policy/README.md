# Dummy HR Policy API

A standalone FastAPI app for learning LangGraph tool calls. Every valid query
returns the same five fictional HR policy chunks, in the same order: leave,
working hours, remote work, expenses, and employee conduct. All policy text
lives directly in `main.py`.

This is a placeholder retrieval service. It does not perform similarity search
or use embeddings, a database, authentication, or external services.

## Setup and run

Use Python 3.10 or later. From the `LangGraph` directory, run in PowerShell:

```powershell
cd HR_policy
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001
```

If Python is not on your PATH, create the virtual environment with the installed
interpreter's full path instead:

```powershell
& "C:\Users\salmo\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
```

The API runs at http://127.0.0.1:8001, allowing the employee API to keep using
port `8000`. Open http://127.0.0.1:8001/docs for interactive API documentation.
The schema is available at http://127.0.0.1:8001/openapi.json.
Press Ctrl+C to stop the server.

## Search policies

Send `POST /policies/search` with a JSON body containing a nonblank string query:

```powershell
$body = @{ query = "What is the leave policy?" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8001/policies/search" `
    -Method Post -ContentType "application/json" -Body $body |
    ConvertTo-Json -Depth 5
```

The response contains `query` (the submitted string, preserved exactly) and
`chunks` (exactly five objects). Each chunk contains `chunk_id`, `title`, and
`content`. Chunk IDs are `HR001` through `HR005`. Different questions return
identical chunks in identical order; the chunks have no similarity scores.

A missing, empty, whitespace-only, null, or nonstring query returns HTTP `422`.
All policy text is fictional demo data. LangGraph integration will be added
separately.
