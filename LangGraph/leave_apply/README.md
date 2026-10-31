# Leave Application API

A standalone FastAPI app that accepts employee details and an applied leave
request, then appends the record to `leave_applications.jsonl` next to `main.py`.
The file is created on the first successful submission. Existing records are
preserved, including after restarting the app.

## Setup and run

Use Python 3.10 or later. From the `LangGraph` directory, run in PowerShell:

```powershell
cd leave_apply
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8002
```

If Python is not on your PATH, create the virtual environment using its full
path instead:

```powershell
& "C:\Users\salmo\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
```

Open http://127.0.0.1:8002/docs to try the API. The OpenAPI schema is at
http://127.0.0.1:8002/openapi.json. Port `8002` lets this app run alongside the
employee app on `8000` and HR policy app on `8001`. Press Ctrl+C to stop it.

## Apply for leave

Send `POST /leave-applications` with a nested `employee` object matching the
employee-details API and a nested `leave` object:

```powershell
$body = @{
    employee = @{
        employee_id = "EMP004"
        name = "Arjun Rao"
        email = "arjun.rao@example.com"
        department = "Engineering"
        designation = "Software Engineer"
        joining_date = "2022-03-14"
        manager_id = "EMP002"
        leave_balance = 12
    }
    leave = @{
        start_date = "2026-11-10"
        end_date = "2026-11-12"
        leave_type = "annual"
        reason = "Family vacation"
    }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8002/leave-applications" `
    -Method Post -ContentType "application/json" -Body $body |
    ConvertTo-Json -Depth 5
```

A successful submission returns HTTP `201` with the saved `employee` and
`leave` objects, plus a generated UUID `application_id` and UTC `submitted_at`
timestamp. The same record is stored as one UTF-8 JSON line. Line breaks within
text are escaped so they do not split a record across lines.

All shown fields are required; `manager_id` may be `null`. Strings must not be
blank, dates use `YYYY-MM-DD`, and `leave_balance` must be a nonnegative whole
number. The end date must be on or after the start date; same-day leave is
accepted. Any nonblank leave type is accepted. Invalid requests return HTTP
`422` without appending a record. File write errors return HTTP `500` with
`{"detail": "Could not save leave application"}`.

## Demo behavior

Run one application process. A lock protects appends between requests within
that process. The app stores caller-supplied employee data without calling
other services. It does not approve applications, check policies, deduct leave,
calculate working days or holidays, detect duplicates, or expose stored records
through a retrieval endpoint. There is no authentication or database.
