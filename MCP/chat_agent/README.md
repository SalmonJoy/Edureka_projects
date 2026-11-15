# Terminal chat agent using MCP tools

A small chat agent using **plain Python**, **OpenRouter or Ollama Cloud**, and
the **MCP Python SDK v2**. It discovers the existing HR server's tools instead of
importing its tool functions or calling the three HR APIs directly.

```text
You (terminal)
    |
    v
ChatAgent <----------> OpenRouter or Ollama model
    |                 chooses a tool or returns an answer
    v
MCP client ----------> HR MCP server ----------> three HR HTTP APIs
    ^                         |
    +-------- tool result ----+
```

The model decides which tools to use. Python performs the calls, adds the
results to the conversation, and asks the model again. No LangChain or
LangGraph is involved.

## 1. Install

Use PowerShell in the **MCP parent directory**, not inside `chat_agent`:

```powershell
cd "C:\Salmon\Edureka\AI agents\MCP"
uv pip install --python .venv\Scripts\python.exe -r chat_agent\requirements.txt
```

This uses the virtual environment already created for the MCP project.
If you need to recreate it, run `uv venv --python 3.11 .venv` first and also
install `MCP_server\requirements.txt` to run the server.

## 2. Configuration

The agent loads this existing shared file automatically:

```text
C:\Salmon\Edureka\AI agents\.env
```

Its location is resolved relative to the package, so configuration loading
does not depend on the terminal's current directory. File values override
inherited environment variables. The agent does not edit the file or print
the API key.

| Variable | Default / purpose |
| --- | --- |
| `MODEL_PROVIDER` | `openrouter` when an OpenRouter key is present; otherwise `ollama` |
| `OPEN_ROUTER_API_KEY` | Your OpenRouter key; `OPENROUTER_API_KEY` is also accepted |
| `OPENROUTER_MODEL` | `openrouter/free` (OpenRouter's free model router) |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` |
| `OLLAMA_API_KEY` | Required when using Ollama Cloud |
| `OLLAMA_HOST` | `https://ollama.com` (the server origin, without `/api/chat`) |
| `OLLAMA_MODEL` | `gpt-oss:20b` |
| `MCP_PORT` | `8003`; used to construct the default MCP URL |
| `MCP_URL` | Optional full URL; overrides the default derived from `MCP_PORT` |

Your existing `OPEN_ROUTER_API_KEY` is enough to select OpenRouter automatically.
For example, these are the relevant entries you can set in that file:

```dotenv
OPEN_ROUTER_API_KEY=your-existing-key
OPENROUTER_MODEL=openrouter/free
MCP_URL=http://127.0.0.1:8003/mcp
```

To choose a provider explicitly, set `MODEL_PROVIDER=openrouter` or
`MODEL_PROVIDER=ollama`. The selected provider must have its own key. Ollama
continues to use `OLLAMA_API_KEY`, `OLLAMA_HOST`, and `OLLAMA_MODEL`.

Other entries in the shared file are loaded but do not enable integrations
such as databases or tracing in this agent.

## 3. Run

Keep the employee, policy, and leave APIs running on ports 8000, 8001, and 8002.
Start the MCP server in a separate terminal from the MCP parent directory:

```powershell
cd "C:\Salmon\Edureka\AI agents\MCP"
.\.venv\Scripts\python.exe -m MCP_server.server
```

Then start the agent in another terminal:

```powershell
cd "C:\Salmon\Edureka\AI agents\MCP"
.\.venv\Scripts\python.exe -m chat_agent.main
```

It displays the provider, model, MCP address, and discovered tool names. Type `exit`
or `quit` to stop. EOF and Ctrl+C also exit. Run the package with `-m` so
imports such as `from chat_agent.config import load_settings` resolve.

## Example conversations

Try these read-only requests first:

```text
You: What is the name and leave balance of EMP001?
[tool] get_employee {"employee_id": "EMP001"}
Assistant: ...answer based on the employee record...

You: What department do they work in?
Assistant: ...answer using the same conversation history...

You: What is the annual leave policy?
[tool] search_policies {"query": "annual leave policy"}
Assistant: ...summary based on the returned policy chunks...

You: Compare the leave balances of EMP001 and EMP003.
[tool] get_employees {"employee_ids": ["EMP001", "EMP003"]}
Assistant: ...comparison based on the returned employee records...

You: Show all employees.
[tool] get_employees {"employee_ids": -1}
Assistant: ...details for EMP001 through EMP010...
```

The batch tool returns individual errors alongside successful employee records.
Restart both the MCP server and the chat agent after adding a new tool so it
appears in the discovered tool list.

For submission, explicitly request it and provide the leave details:

```text
Submit annual leave for EMP001 from 2026-12-01 to 2026-12-02.
Reason: Personal time.
```

This writes an application in the learning API. The agent is instructed to
fetch the complete employee record first and ask for missing leave details.
It passes the employee record and leave object to `apply_leave` through MCP.
Submission does **not** approve leave or change balances.

Tool calls and arguments are shown so you can follow the process. Intermediate
model reasoning is kept in the model conversation when returned, but it is not
printed. Conversation history exists only in memory and resets on exit.

The policy API is a dummy retrieval API: all questions receive the same five
fictional policy chunks without ranking or similarity search.

## Read the code in this order

| Module | Responsibility |
| --- | --- |
| `main.py` | Open connections, discover tools, read terminal input, display answers |
| `config.py` | Load `.env` and return a small `Settings` dataclass |
| `agent.py` | Keep message history and run the model/tool loop |
| `mcp_tools.py` | Convert MCP tool descriptions into model tool definitions and execute calls |
| `model.py` | Ollama HTTP adapter and shared model-error type |
| `openrouter_model.py` | OpenRouter HTTP adapter and tool-call format conversion |

The main interfaces are intentionally small:

```python
await tools.discover()                         # MCP tools/list
reply = await model.chat(messages, tools.definitions)
result = await tools.call(name, arguments)     # MCP tools/call
text = await agent.ask("Look up EMP001")       # one user turn
```

For each user turn, `ChatAgent.ask`:

1. Appends the user message to history.
2. Sends history and discovered tool schemas to the selected model provider.
3. Appends the assistant message, including any tool calls.
4. Executes the requested tools sequentially through MCP.
5. Appends each result as a `tool` message and returns to step 2.
6. Returns the final answer when the model stops requesting tools.

MCP tool results are sent to the model as JSON with `is_error` and `result`
fields. Structured data is preserved; text-only failures remain readable.
Unknown tools become error results without being sent to the server.

### Provider adapters

Both adapters expose the same `chat(messages, tools)` interface, so the agent
loop stays the same when switching providers. No new dependency is needed
for OpenRouter.

OpenRouter uses `/api/v1/chat/completions`. Its tool arguments are JSON strings,
and results must include the matching `tool_call_id`. The adapter translates
argument strings to Python dictionaries and back; the agent keeps each call's
ID on the corresponding result. Provider `reasoning_details`, when present,
are preserved for follow-up requests without being printed.

For Ollama, the installed Python SDK narrowed the nested MCP input schemas,
dropping references needed for `employee` and `leave`. A short `httpx` POST
to `/api/chat` preserves the full schema, including `$defs` and `$ref`, without
custom SDK classes or schema rewriting. No Ollama SDK is required.


## Errors and limits

- Model requests have a 120-second timeout. MCP calls have a 30-second read
  timeout; the server's upstream API requests use their existing timeout.
- A turn stops after 10 model requests and retains completed tool results in
  history. There are no automatic HTTP retries.
- An identical `apply_leave` call repeated within a single user turn reuses
  the first result, including failures, rather than submitting twice. This
  is not persistent deduplication across separate turns or sessions.
- After a failed or timed-out submission, its outcome may be unknown; the
  prompt tells the model not to automatically retry it.
- HTTP 401/403 from Ollama: check the cloud key and access. HTTP 404: check
  the host and model. HTTP 402 (Payment Required): check the cloud account's
  usage or billing. These errors do not trigger tool execution.
- OpenRouter HTTP 401: check `OPEN_ROUTER_API_KEY`. HTTP 429: the selected
  model or account is rate-limited. HTTP 402: check account limits or credits.
  The default `openrouter/free` routes only to free models; availability can vary.
- If the MCP connection fails, verify `MCP_URL` and start the server before
  launching the agent. The agent does not start or stop the server itself.

## Tests

From the MCP parent directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s chat_agent\tests -v
```

Tests mock both model providers and MCP calls. They cover configuration precedence, nested
schema preservation all the way to the outgoing model request, normal chat,
follow-up history, multiple tools, employee-to-leave flow, duplicate submissions,
tool errors, model errors, timeouts, limits, and clean connection shutdown.
No cloud access or live leave submissions are needed.

The OpenRouter adapter is also checked for bearer authentication, argument
conversion, tool-call ID matching, and preservation of nested schemas.

## References

- [Ollama tool calling and agent loops](https://docs.ollama.com/capabilities/tool-calling)
- [Ollama chat API](https://docs.ollama.com/api/chat)
- [Ollama cloud authentication](https://github.com/ollama/ollama-python#cloud-api-ollamacom)
- [MCP Python client](https://py.sdk.modelcontextprotocol.io/client/)
- [OpenRouter tool calling](https://openrouter.ai/docs/guides/features/tool-calling)
- [OpenRouter free model router](https://openrouter.ai/docs/guides/routing/routers/free-models-router)
