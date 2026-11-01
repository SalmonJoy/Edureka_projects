# Streamlit Leave Assistant

A chat UI for the existing LangGraph FastAPI backend. It supports natural
language leave requests, clarification, policy/balance review, explicit
submission or decline, cancellation, conversation reuse, and workflow recovery.
All UI code is in `Langchain_steamlit`; existing backend code is unchanged.

## Start the services

Keep the existing employee, HR policy, leave application, and workflow API
services running:

| Service | URL |
| --- | --- |
| Employee details | `http://127.0.0.1:8000` |
| HR policies | `http://127.0.0.1:8001` |
| Leave application storage | `http://127.0.0.1:8002` |
| LangGraph workflow API | `http://127.0.0.1:8003` |

If the workflow API is not running, use a separate PowerShell terminal:

```powershell
cd "C:\Salmon\Edureka\AI agents\LangGraph\langGraph_PoC\langGraph FastAPI"
& "C:\Users\salmo\AppData\Local\Programs\Python\Python311\python.exe" -m uvicorn app:app --reload --port 8003
```

The backend supplies the fixed employee `EMP004`, Ollama `gpt-oss:20b`, MongoDB
conversation memory, SQLite checkpoints, and the leave JSONL storage. The UI
does not read the shared `.env`, database credentials, or Ollama API key.

## Install and launch the UI

Use Python 3.10 or later. In another PowerShell terminal:

```powershell
cd "C:\Salmon\Edureka\AI agents\LangGraph\Langchain_steamlit"
& "C:\Users\salmo\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

With dependencies already installed, you can use the existing interpreter:

```powershell
& "C:\Users\salmo\AppData\Local\Programs\Python\Python311\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Open http://127.0.0.1:8501. Press Ctrl+C to stop the UI. The API URL defaults to
`http://127.0.0.1:8003`; change it in the sidebar's **API connection** form or set
the `LANGGRAPH_API_URL` environment variable before starting Streamlit.

## Use the assistant

1. Enter future leave dates and a reason in chat, for example:
   `I need 3 working days of annual leave starting 2026-11-16 for a family vacation.`
   Choose future dates with enough policy notice for the day you run the demo.
2. Answer clarification questions in chat. The UI sends the current prompt ID.
3. Review the employee, dates, working-day count, reason, available balance,
   and policy eligibility reasons in the review card.
4. Click **Submit application** to explicitly confirm, or **Decline** to end the
   application without submission. Chat text cannot approve an application.
5. A successful submission shows the application receipt. Ineligible requests,
   cancellations, errors, uncertain outcomes, and storage warnings have separate
   status messages.

Use **Cancel workflow** during clarification or other unfinished work. After a
workflow finishes, the next chat message starts another leave request in the
same conversation and requires a fresh confirmation.

Requests wait for the API response. The processing indicator reflects that wait;
it does not simulate per-node progress. Controls are disabled during an action.

## Conversation and workflow recovery

- The sidebar displays the current **Conversation ID** and **Workflow ID**.
  Copy them if needed.
- **Refresh status** performs a read-only GET. It never resumes or submits.
- **Load workflow** restores the latest saved status using its workflow ID.
  The UI also bookmarks the active ID as `?thread_id=...` in the URL; refreshing
  that browser page restores status through a GET, without replaying an answer.
- **Continue workflow** explicitly sends an empty resume request. It recovers
  safe unfinished work or returns an existing saved result. It never supplies
  confirmation automatically. The API blocks ambiguous submission recovery.
- **Reuse a conversation** selects an existing MongoDB conversation ID for new
  requests. The API uses its earlier messages as context.
- **New conversation** clears the local view and creates a new conversation ID.
  Switching views does not cancel previous saved workflows.

Only messages from the current UI session and the latest recovered workflow
response are displayed. The current API does not expose the full saved MongoDB
transcript. SQLite and MongoDB persistence stay entirely in the backend, and
workflow IDs must belong to the workflow API's own SQLite database.

The Streamlit session retains messages and known receipts across ordinary
reruns. Reruns do not repeat POSTs. A new browser session restores the latest
workflow through its URL or a manually entered ID, rather than reconstructing
the full chat transcript.

## Errors and retries

GET requests have a 10-second timeout; POST requests have a 600-second timeout
to allow for LLM processing. No POST is automatically retried.

- A stale or conflicting answer (`409`) triggers a read-only refresh. Review
  the resulting status before another explicit action.
- Invalid input (`422`), missing workflows (`404`), and storage failures (`503`)
  are displayed without discarding a known receipt.
- If a POST times out or its result is unreadable, actions pause until saved
  status can be checked. If starting a workflow failed before its ID was
  received, the UI cannot discover that ID through the current API: load it
  manually once known and do not assume the operation failed.
- Unknown submission outcomes must be checked in the backend's saved leave
  applications before starting another submission. A storage warning attached
  to a known receipt does not turn the saved application into a failed one.

Run one worker for the existing workflow API and one active writer per
conversation, as required by its persistence model.

## Files and tests

| File | Purpose |
| --- | --- |
| `app.py` | Streamlit chat, sidebar, review card, and receipts |
| `api_client.py` | HTTP API contracts, response validation, and timeouts |
| `ui_state.py` | Session transitions, transcript deduplication, one-shot actions |
| `tests/test_ui.py` | HTTPX MockTransport, state, and Streamlit AppTest tests |

From this folder, run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use mocked HTTP and a fake workflow API. They cover requests,
clarification, approval/decline, cancellation, recovery, refresh, conversation
reuse, status rendering, rerun safety, timeouts, stale prompts, storage warnings,
and preserving receipts after errors. They submit no live leave application.
