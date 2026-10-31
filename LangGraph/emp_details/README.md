# Dummy Employee Details API

A standalone FastAPI app for learning LangGraph tool calls. Ten fictional
employees (`EMP001` through `EMP010`) are hardcoded in `main.py`.
No database or authentication is required.

## Setup and run

Use Python 3.10 or later. From the `LangGraph` directory, run these commands
in PowerShell:

```powershell
cd emp_details
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

The server runs at http://127.0.0.1:8000. Press Ctrl+C to stop it.
Open http://127.0.0.1:8000/docs for interactive API documentation.
The OpenAPI schema is available at http://127.0.0.1:8000/openapi.json.

If Python is not on your PATH, replace `python` in the virtual environment
creation command with the full path to your Python executable.

## Look up an employee

`GET /employees/{employee_id}` accepts a case-sensitive employee ID.

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/employees/EMP001"
```

Example JSON response (HTTP 200):

```json
{
  "employee_id": "EMP001",
  "name": "Ananya Sharma",
  "email": "ananya.sharma@example.com",
  "department": "Management",
  "designation": "General Manager",
  "joining_date": "2019-04-15",
  "manager_id": null,
  "leave_balance": 20
}
```

Joining dates use `YYYY-MM-DD`. `manager_id` refers to another employee or
is `null` for the top-level manager. `leave_balance` is available leave in
whole days.

Unknown IDs, including `emp001`, return HTTP 404:

```json
{"detail": "Employee not found"}
```

To add or edit placeholder records, update `EMPLOYEES` in `main.py`.
LangGraph integration and employee-editing endpoints are outside this app.
