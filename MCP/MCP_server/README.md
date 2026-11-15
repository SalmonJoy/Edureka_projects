# HR MCP server in Python

A small learning project that exposes three existing HTTP APIs as four MCP tools.
The server uses the **official MCP Python SDK v2** and Streamable HTTP.

```text
MCP client
    |
    | MCP tools/list or tools/call
    v
HR MCP server -- http://127.0.0.1:8003/mcp
    |
    +-- get_employee ----> Employee API :8000
    +-- get_employees ---> Employee API :8000 (multiple lookups)
    +-- search_policies -> Policy API   :8001
    +-- apply_leave -----> Leave API    :8002
```

The client discovers the tool names, descriptions, and input schemas through
MCP. A tool then calls its HTTP API and returns JSON. The batch employee tool
combines individual lookup responses. No LLM or API key is needed for this example.

## 1. Set up

Run these commands in PowerShell from the **MCP parent directory**, which contains
both `MCP_server` and `.venv`. Python 3.11+ and `uv`
are needed. These commands create a separate environment for this project.

```powershell
cd "C:\Salmon\Edureka\AI agents\MCP"
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r MCP_server\requirements.txt
```

The project uses SDK **v2** (`from mcp.server import MCPServer`). Some older
tutorials use the v1 `FastMCP` class; use this project's virtual environment
so those versions do not get mixed together.

## 2. Start the server

Keep the three existing APIs running in their own processes:

| API | Documentation |
| --- | --- |
| Employee | <http://127.0.0.1:8000/docs> |
| HR policy | <http://127.0.0.1:8001/docs> |
| Leave application | <http://127.0.0.1:8002/docs> |

Start the MCP server in another terminal:

```powershell
.\.venv\Scripts\python.exe -m MCP_server.server
```

The MCP endpoint is **http://127.0.0.1:8003/mcp**. It speaks the MCP protocol;
it is not a Swagger documentation page. Leave this terminal running and use
Ctrl+C to stop it.

## 3. Try the read-only example

In a second terminal, from the MCP parent directory:

```powershell
.\.venv\Scripts\python.exe -m MCP_server.example_client
```

This lists all four tools, fetches `EMP001`, looks up `EMP001` and `EMP003`
in one batch, and asks a policy question.
It does not submit leave. A successful result is available as the Python
dictionary `result.structured_content`; a failed call has `result.is_error`
set to `True` and an explanation in its text content.

## Tools

| Tool | Arguments | API call |
| --- | --- | --- |
| `get_employee` | `employee_id` | `GET :8000/employees/{employee_id}` |
| `get_employees` | `employee_ids`: non-empty list of strings, or integer `-1` | One employee GET per requested ID |
| `search_policies` | `query` | `POST :8001/policies/search` |
| `apply_leave` | `employee`, `leave` | `POST :8002/leave-applications` |

Examples of the argument objects clients send:

```json
{"employee_id": "EMP001"}
```

```json
{"query": "What is the annual leave policy?"}
```

Employee IDs are case-sensitive (`EMP001` to `EMP010`). Policy search is a
dummy retrieval API: it echoes the query and returns the same five fictional
policy chunks for every query, without ranking or similarity search.

### Multiple employee details

Pass selected IDs as a list:

```json
{"employee_ids": ["EMP001", "EMP003", "EMP005"]}
```

Or pass the integer `-1` to fetch all ten fictional employees, `EMP001` through
`EMP010`. Use a number, not the string `"-1"`:

```json
{"employee_ids": -1}
```

For example, in an MCP client:

```python
result = await client.call_tool(
    "get_employees", {"employee_ids": ["EMP001", "UNKNOWN"]}
)
rows = result.structured_content["results"]
for row in rows:
    if row["error"] is not None:
        print(row["employee_id"], row["error"])
    else:
        print(row["employee"]["name"])
```

The response has one entry per requested ID:

```json
{
  "results": [
    {
      "employee_id": "EMP001",
      "employee": {"employee_id": "EMP001", "name": "Ananya Sharma", "...": "other employee fields"},
      "error": null
    },
    {
      "employee_id": "UNKNOWN",
      "employee": null,
      "error": "API at http://127.0.0.1:8000/employees/UNKNOWN returned HTTP 404: ..."
    }
  ]
}
```

Lookups run concurrently with `asyncio.gather`, while results preserve input
order, ID case, and duplicate IDs. A failed lookup does not discard successes.
Even if every lookup fails, a valid batch returns the `results` object with
an error in each entry. Check those errors rather than only `result.is_error`.
Empty lists and unsupported input types are rejected before HTTP requests.

The `-1` option uses the known ten IDs in this learning dataset; the upstream
API does not expose a list-all endpoint. No upstream API changes are needed.

After adding or changing tools, restart the MCP server and the chat agent.
The agent discovers `get_employees` automatically. Try asking it to compare
`EMP001` and `EMP003`, or to show all employees.

### Submitting leave

Call `get_employee` first, then pass its entire response as `employee`.
The leave API requires every employee field, including `manager_id` (which
can be `null`). Dates use `YYYY-MM-DD`.

The following is an **optional example**. Running it records a real application
in the local learning API's JSONL file; it is not part of `example_client.py`.

```python
import asyncio
import json

from mcp import Client
from MCP_server.config import MCP_URL


async def main():
    async with Client(MCP_URL) as client:
        employee_result = await client.call_tool(
            "get_employee", {"employee_id": "EMP001"}
        )
        if employee_result.is_error:
            print(employee_result.content)
            return

        result = await client.call_tool(
            "apply_leave",
            {
                "employee": employee_result.structured_content,
                "leave": {
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-02",
                    "leave_type": "annual",
                    "reason": "Personal time",
                },
            },
        )
        if result.is_error:
            print(result.content)
            return
        print(json.dumps(result.structured_content, indent=2))


asyncio.run(main())
```

The returned record includes `employee`, `leave`, `application_id`, and
`submitted_at`. Submission does **not** approve leave or change balances.
Repeated calls create separate applications. There are no automatic retries;
after a timeout, the API may already have saved the application.

## How the modules fit together

```text
server.py            Creates the server, registers tools, starts HTTP
config.py            API addresses, MCP port, request timeout
models.py            Pydantic Employee and LeaveDetails inputs
api_client.py        Shared HTTP request and error handling
tools/
    __init__.py
    employees.py     get_employee and get_employees
    policies.py      search_policies
    leave.py         apply_leave
example_client.py    Read-only MCP client
requirements.txt     Project dependencies
tests/
    test_tools.py    MCP calls backed by mocked HTTP responses
```

Start reading with `server.py`, then `tools/employees.py`, then `api_client.py`.
The tool functions are ordinary async Python functions. `mcp.tool()(function)`
registers a function just like writing `@mcp.tool()` above it, while keeping
registration in one place. The SDK derives tool schemas from type hints and
descriptions from docstrings.

`models.py` describes the nested leave inputs. Pydantic validates them before
the tool runs. `model_dump(mode="json")` converts date objects back into the ISO
strings the HTTP API expects. The individual tools forward their API responses;
`get_employees` collects employee records and errors under `results`.

`api_client.py` makes one request with a 10-second timeout, closes its HTTP
client afterward, and raises `ToolError` for HTTP errors, unavailable APIs,
timeouts, or invalid response JSON. Raising a tool error lets MCP mark the
call as failed instead of returning an error string as a successful result.

To add another API later: create a tool module, call the shared HTTP helper,
then import and register the function in `server.py`.

## Configuration

Defaults work with the supplied APIs. Override them with environment variables
before starting the server:

| Variable | Default |
| --- | --- |
| `EMPLOYEE_API_URL` | `http://127.0.0.1:8000` |
| `POLICY_API_URL` | `http://127.0.0.1:8001` |
| `LEAVE_API_URL` | `http://127.0.0.1:8002` |
| `MCP_PORT` | `8003` |

For example, if port 8003 is occupied:

```powershell
$env:MCP_PORT = "8010"
.\.venv\Scripts\python.exe -m MCP_server.server
```

Set the same `MCP_PORT` in the example client's terminal. The server binds
to `127.0.0.1` and has no authentication; it is intended for local learning.
The `.env` file in the parent project is not loaded or needed here.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s MCP_server\tests -v
```

Tests connect to the MCP server in memory and intercept every upstream HTTP
request with `httpx.MockTransport`. They check tool discovery, nested input
validation, request routing and payloads, response forwarding, API errors,
timeouts without retries, malformed responses, and batch lookup behavior.
Batch checks include `-1`, invalid inputs, ordering, duplicates, concurrent
execution, partial failures, and all failed lookups. The three live APIs do
not need to be running, and the tests do not create leave applications.

## Official SDK references

- [Tools and typed inputs](https://py.sdk.modelcontextprotocol.io/servers/tools/)
- [Running a Streamable HTTP server](https://py.sdk.modelcontextprotocol.io/run/)
- [Handling tool errors](https://py.sdk.modelcontextprotocol.io/servers/handling-errors/)
- [Python MCP clients](https://py.sdk.modelcontextprotocol.io/client/)
