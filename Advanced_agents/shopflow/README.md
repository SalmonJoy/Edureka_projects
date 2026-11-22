# Shopflow release lab

A small **FastAPI API** and **Streamlit UI** for learning about release readiness and testing future CrewAI tools. Shopflow is a fictional shopping application covering catalog, cart, checkout, payments, inventory, and orders.

This project supplies evidence and deterministic policy checks. It does **not** create CrewAI agents, run real tests/scanners, or deploy software. No API keys or external services are required.

## 1. Install and run

Use Python 3.11+ and `uv`. In PowerShell:

```powershell
cd 'C:\Salmon\Edureka\AI agents\Advanced_agents\shopflow'
uv sync
```

Start the API in the first terminal:

```powershell
uv run uvicorn backend.main:app --reload --port 8001
```

In a second terminal, change to the same `shopflow` folder and start the UI:

```powershell
uv run streamlit run streamlit_app.py --server.port 8503
```

- UI: <http://localhost:8503>
- Interactive Swagger API documentation: <http://127.0.0.1:8001/docs>
- OpenAPI schema for tool development: <http://127.0.0.1:8001/openapi.json>
- Health check: <http://127.0.0.1:8001/health>

The UI's API base URL defaults to `http://127.0.0.1:8001`. Change it in the sidebar or set `$env:SHOPFLOW_API_URL = 'http://127.0.0.1:8001'` before starting Streamlit. This project does not load the shared `.env` used by other demos.

## 2. Explore the three releases

| Release ID | Version | Initial scenario | Staging | Production |
|---|---|---|---|---|
| `rel-100` | 1.0.0 | Ready | Pass | Pass |
| `rel-110` | 1.1.0 | Needs attention | Pass with warnings | Fail |
| `rel-120` | 1.2.0 | Blocked | Fail | Fail |

Each candidate contains **10 test results, 4 bugs, 4 security checks, 3 security findings, 4 required documents, and release notes**. IDs such as `TEST-001` are scoped to a release, so each candidate can represent the same test with different results.

The ready release has all tests passed and approved documentation, but still has an open low-severity cosmetic bug. The needs-attention release has 8 passed tests, 1 failed noncritical test, 1 skipped noncritical test, an unresolved high-severity bug, documentation gaps, and draft notes. The blocked release also has failed critical tests, critical bugs/findings, and incomplete security execution evidence.

The `initial_scenario` field describes the seed, not a permanent release status. Summaries and readiness are recalculated after edits.

### Try a remediation walkthrough

1. Select **1.1.0**, open **Deployment**, and compare staging with production. Read each gate's actual evidence and explanation.
2. In **Tests**, change `TEST-009` and `TEST-010` to `passed`.
3. In **Bugs**, resolve `BUG-002`.
4. In **Documentation**, mark `DOC-002` and `DOC-004` complete, then approve the release notes.
5. Return to **Deployment** with production selected. All seven gates now pass. The open medium-severity security finding is allowed by this dummy policy.
6. Click **Reset selected release** to restore 1.1.0's original blockers. Edits to other releases remain intact.

You can also turn a ready release into a blocked one: fail a critical test, reopen a critical finding, or mark a required security check `not_run`.

## 3. Understand deployment policies

These are explicit demonstration policies, not production recommendations.

| Gate | Staging | Production |
|---|---|---|
| Test pass rate | At least 80% | 100% |
| Critical tests | At least one exists and all pass | Same |
| Bugs | No unresolved critical bugs | No unresolved critical/high bugs |
| Security findings | No open critical findings | No open critical/high findings |
| Required security checks | At least one exists and all pass | Same |
| Required documentation | Gaps produce warnings | At least one required document exists and all are complete |
| Release notes | Unapproved notes produce warnings | Approved |

```text
Pass rate = passed tests / total tests × 100
Total tests includes passed, failed, and skipped tests.
Ready = every deployment gate passes.
```

No test results means the release fails the test gate. A skipped critical test fails the critical-test gate. Open and in-progress bugs are both unresolved. Resolved bugs and findings do not block readiness. Integer-count comparisons enforce the percentage threshold, rather than a rounded display value.

Security checks and findings are separate: `passed` means the simulated scan/check execution completed successfully. It does not mean every finding was resolved. This lets a security analyst distinguish missing scan evidence from known vulnerabilities.

Staging returns passing advisory gates for documentation/notes plus explicit warnings when those areas have gaps. Every gate includes `id`, `name`, `passed`, `expected`, `actual`, and `reason`. The result includes `ready`, `passed_gates`, `total_gates`, and `warnings`.

Readiness checks are pure calculations. They neither approve nor deploy a release, and the policies have no update endpoint.

## 4. API reference

Global endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | API health |
| GET | `/releases` | Candidate metadata and initial scenarios |
| GET | `/releases/{release_id}` | One candidate's metadata |
| GET | `/deployment-rules/{environment}` | Read-only `staging` or `production` policy |

The following paths are relative to **`/releases/{release_id}`**:

| Method | Path | Filters / body |
|---|---|---|
| GET | `/summary` | Current QA, bug, security, and documentation counts; readiness for both environments |
| GET | `/tests` | Optional `status`, `module`, `critical` |
| GET | `/tests/summary` | Totals, pass rate, and critical-test counts |
| PATCH | `/tests/{test_id}` | Required `status`; `failure_reason` required when failed |
| GET | `/bugs` | Optional `status`, `severity`, `module`, `unresolved_only` |
| PATCH | `/bugs/{bug_id}` | `status` and/or `severity` |
| GET | `/security/report` | Consolidated counts, check details, and finding details |
| GET | `/security/checks` | Optional `status` |
| PATCH | `/security/checks/{check_id}` | Required `status` and nonempty `details` |
| GET | `/security/findings` | Optional `status`, `severity` |
| PATCH | `/security/findings/{finding_id}` | `status` and/or `severity` |
| GET | `/documents` | Optional `status`, `required` |
| PATCH | `/documents/{document_id}` | Required `status` |
| GET | `/release-notes` | Current notes content and approval status |
| PATCH | `/release-notes` | `status` and/or nonempty `content` |
| POST | `/deployment-check` | `{"environment": "production"}` or `staging` |
| POST | `/reset` | No body; restore only this candidate |

Filter values are combined. A valid search with no matches returns `[]`.

| Field | Values |
|---|---|
| Test status | `passed`, `failed`, `skipped` |
| Bug status | `open`, `in_progress`, `resolved` |
| Severity | `critical`, `high`, `medium`, `low` |
| Security-check status | `passed`, `failed`, `not_run` |
| Finding status | `open`, `resolved` |
| Document status | `draft`, `in_review`, `complete` |
| Release-notes status | `draft`, `in_review`, `approved` |
| Module | `catalog`, `cart`, `checkout`, `payments`, `inventory`, `orders` |

Updates validate enum values and reject unsupported fields. A failed test requires a nonblank failure reason; changing it to passed/skipped clears the old reason. Test durations are dummy sample values, not measured executions; marking a test skipped sets its duration to zero.

- `404`: unknown release or item within that release.
- `422`: invalid filters, invalid statuses, empty update bodies, missing failure details, or unsupported update fields.
- `405`: unsupported operations, such as trying to edit deployment policies.

### PowerShell example

```powershell
$base = 'http://127.0.0.1:8001'
Invoke-RestMethod "$base/releases/rel-110/tests?status=failed"
Invoke-RestMethod "$base/releases/rel-110/bugs?unresolved_only=true&severity=high"
Invoke-RestMethod "$base/releases/rel-110/security/report"

$check = @{ environment = 'production' } | ConvertTo-Json
Invoke-RestMethod "$base/releases/rel-110/deployment-check" -Method Post -ContentType 'application/json' -Body $check

$fix = @{ status = 'passed' } | ConvertTo-Json
Invoke-RestMethod "$base/releases/rel-110/tests/TEST-010" -Method Patch -ContentType 'application/json' -Body $fix

Invoke-RestMethod "$base/releases/rel-110/reset" -Method Post
```

## 5. Use these APIs with your future crew

| Future role | Useful evidence and tools |
|---|---|
| Release manager | Release summary, environment rules, readiness gates, release notes, deployment/rollback documentation |
| QA engineer | Passed/failed/skipped details, critical-test results, unresolved bugs and severity |
| Security analyst | Consolidated security report, scan execution status, open findings and severity |
| Documentation specialist | Required documents, completion gaps, release-note content and approval status |

The Swagger/OpenAPI schema describes the request and response models for future HTTP tool wrappers. Start with GET reports; use PATCH only when a tool is intended to simulate evidence changes. A crew can discuss the evidence and recommend go/no-go decisions without any agent code being included here.

## 6. Learn the modules

```text
shopflow/
├── streamlit_app.py          # Direct Streamlit entry point and sidebar selection
├── backend/
│   ├── main.py              # FastAPI factory, routers, and health check
│   ├── models.py            # Pydantic inputs, outputs, statuses, and validation
│   ├── data.py              # Shared sample cases and three scenario seeds
│   ├── store.py             # Independent in-memory stores and release-scoped lookup/reset
│   ├── services.py          # Derived summaries and deterministic readiness gates
│   └── routers/
│       ├── releases.py      # Metadata, summary, and reset
│       ├── qa.py            # Tests and bugs
│       ├── security.py      # Reports, checks, and findings
│       ├── documentation.py # Documents and release notes
│       └── deployment.py    # Read-only rules and readiness checks
├── frontend/
│   ├── api_client.py        # HTTP requests, timeouts, and readable errors
│   └── views.py             # Six feature screens and editing forms
├── tests/                   # API, client, policy, and Streamlit behavior tests
├── pyproject.toml
└── uv.lock
```

Request flow: **UI or future tool → HTTP client → FastAPI router → store/services → validated JSON response**. The UI imports no backend data and duplicates no release-policy calculations. Only the selected UI section loads its evidence; responses are fetched again after edits rather than cached as stale readiness decisions.

`APIRouter` groups related endpoints. Pydantic documents responses and validates request bodies. `Depends(get_store)` injects the current app's store. `create_app()` gives tests their own independent data. Streamlit forms batch edits until submission, while session state holds UI feedback and editor-reset tokens.

All edits are shared by clients of one backend process. Nothing is written to disk. Reset replaces the selected candidate with a deep copy of its seed; restarting or using Uvicorn `--reload` restores every candidate. Use one API worker for this demo. This local teaching app has no authentication or real deployment integration.

## 7. Run checks

```powershell
uv run pytest -q
uvx ty check --python .venv\Scripts\python.exe backend frontend streamlit_app.py tests
```

Tests need no running servers or credentials. They verify the staging/production matrix, filtering, test summaries, skipped/missing evidence, independent security gates, remediation, release-scoped reset, validation, readable HTTP errors, and UI forms. AppTest connects the real UI HTTP client to a fresh FastAPI test client in-process.
