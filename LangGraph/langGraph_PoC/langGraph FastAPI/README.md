# LangGraph Leave Workflow FastAPI

A FastAPI wrapper that imports the existing `langGraph_PoC` graph, clients,
MongoDB message memory, and SQLite helpers. All wrapper code lives in this
folder. The existing Python files and terminal app are unchanged.

## Setup and run

In PowerShell, using Python 3.10 or later:

```powershell
cd "C:\Salmon\Edureka\AI agents\LangGraph\langGraph_PoC\langGraph FastAPI"
& "C:\Users\salmo\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --reload --port 8003
```

If the requirements are already installed in your Python environment, run:

```powershell
& "C:\Users\salmo\AppData\Local\Programs\Python\Python311\python.exe" -m uvicorn app:app --reload --port 8003
```

Open http://127.0.0.1:8003/docs for Swagger UI. The OpenAPI schema is at
http://127.0.0.1:8003/openapi.json. Press Ctrl+C to stop.

The existing `C:\Salmon\Edureka\AI agents\.env` supplies `OLLAMA_API_KEY` and
`MONGODB_URI`. Settings, model (`gpt-oss:20b`), employee (`EMP004`), and API URLs
come from the original modules. Keep the three existing FastAPI services running:

| Service | Base URL |
| --- | --- |
| Employee details | `http://127.0.0.1:8000` |
| HR policies | `http://127.0.0.1:8001` |
| Leave application storage | `http://127.0.0.1:8002` |

The wrapper uses FastAPI lifespan to validate MongoDB connectivity, open SQLite,
initialize the shared clients and graph, and close resources on shutdown.

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `POST /workflows` | Start a new leave workflow; HTTP `201` |
| `GET /workflows/{thread_id}` | Read saved status without advancing the graph |
| `POST /workflows/{thread_id}/resume` | Answer a prompt or recover unfinished work |
| `POST /workflows/{thread_id}/cancel` | Cancel a pending workflow |

Start and resume requests wait until processing reaches a clarification,
confirmation, or final result. Allow sufficient HTTP client timeout for the
Ollama calls; there is no background job or polling requirement.

Every workflow response contains:

```json
{
  "thread_id": "generated-workflow-id",
  "conversation_id": "generated-or-supplied-conversation-id",
  "status": "clarifying",
  "message": "What is the reason for your leave?",
  "interrupt": {
    "prompt_id": "clarification:0",
    "kind": "clarification",
    "question": "What is the reason for your leave?",
    "details": {}
  },
  "application": null,
  "warnings": []
}
```

`details` includes known extracted data for clarification or employee, leave,
balance and policy details for confirmation. `application` contains the saved
API receipt after submission. Inspect `status`, not just the HTTP status code:
an ineligible request or a handled model/API failure is still a saved workflow
result (`ineligible`, `error`, or `submission_unknown`). Successful submission
has status `submitted`.

## Example conversation

Use future dates with adequate notice for the dummy annual-leave policy:

```powershell
$base = "http://127.0.0.1:8003"
$body = @{ request = "I need 3 working days of annual leave starting 2026-11-16 for a family vacation." } | ConvertTo-Json
$started = Invoke-RestMethod -Uri "$base/workflows" -Method Post -ContentType "application/json" -Body $body
$started | ConvertTo-Json -Depth 10
```

If clarification is required, answer its current prompt:

```powershell
$answer = @{ prompt_id = $started.interrupt.prompt_id; answer = "Family vacation" } | ConvertTo-Json
$next = Invoke-RestMethod -Uri "$base/workflows/$($started.thread_id)/resume" -Method Post -ContentType "application/json" -Body $answer
```

When `interrupt.kind` is `confirmation`, review its details and explicitly
approve with a JSON Boolean. This is the step that can submit an application:

```powershell
$review = Invoke-RestMethod -Uri "$base/workflows/$($started.thread_id)"
$answer = @{ prompt_id = $review.interrupt.prompt_id; answer = $true } | ConvertTo-Json
$result = Invoke-RestMethod -Uri "$base/workflows/$($started.thread_id)/resume" -Method Post -ContentType "application/json" -Body $answer
$result | ConvertTo-Json -Depth 10
```

Use `$false` to decline, or cancel a pending request:

```powershell
Invoke-RestMethod -Uri "$base/workflows/$($started.thread_id)/cancel" -Method Post
```

Clarification answers must be nonblank strings. Confirmation answers must be
JSON `true` or `false`; strings such as `"yes"` are rejected. Always use the
current `prompt_id`. Old clarification IDs, repeated approval requests after
completion, and invalid cancellation attempts return HTTP `409`.

To use previous conversation messages for another leave request, pass the
returned `conversation_id` when starting it. Each application gets its own
thread and requires fresh confirmation.

## Persistence and recovery

The wrapper stores checkpoints in **this folder's** `workflow_state.sqlite`,
independent of the terminal app's SQLite file. It retains MongoDB message history
in `Edureka.conversation_messages`. Conversation IDs can be shared with the
terminal app, but the API only finds workflow thread IDs in its own database.

After restarting the server, `GET /workflows/{thread_id}` displays saved status
without calling Ollama, MongoDB, or the leave API. Answer its saved prompt using
the same thread and prompt IDs. To recover safe unfinished work, send an empty
JSON object:

```powershell
Invoke-RestMethod -Uri "$base/workflows/$($started.thread_id)/resume" -Method Post -ContentType "application/json" -Body '{}'
```

An empty resume returns an existing prompt, continues safe unfinished nodes, or
returns a completed result. It never answers a confirmation automatically.
Saved message IDs prevent duplicate prompts and final replies during recovery.
Completed submissions return their receipt without another leave POST.

If the workflow stopped at leave submission without a saved receipt, recovery
returns `409` and does not repeat the POST. Inspect
`leave_apply/leave_applications.jsonl` before submitting again. The existing
leave API has no idempotency key, so a POST and checkpoint cannot be committed
as one atomic operation.

Run **one Uvicorn worker** per API database. Modifying operations are serialized
per conversation, with overlapping calls rejected as `409`. Independent
conversations can run concurrently, and the original graph still fetches the
employee and policies in parallel. Use one active writer per conversation
across both the terminal and API apps.

## Errors

- `404`: the workflow does not exist in the API database.
- `422`: invalid request fields, missing answer pair, or wrong answer type.
- `409`: stale prompt, overlapping conversation operation, completed-workflow
  conflict, or unsafe submission recovery.
- `503`: MongoDB or SQLite storage is unavailable. Error responses include
  workflow/conversation IDs when known. No automatic retry is performed.

A failed confirmation transcript write stops before the graph can submit. If
storage fails after a known successful submission, the response includes the
saved receipt with a warning. It does not repeat the leave POST.

## Files and tests

- `app.py`: FastAPI lifespan, routes, and error responses.
- `api_service.py`: graph orchestration and per-conversation locks.
- `api_models.py`: HTTP request/response models.
- `tests/test_api.py`: HTTP, restart, concurrency, validation, and storage tests.

From this folder, run the wrapper tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use temporary SQLite files and fake LLM/API/MongoDB clients. They do not
submit a live leave application. The existing project's tests remain unchanged.
