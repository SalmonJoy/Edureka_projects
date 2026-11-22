# Shopflow release-readiness crew

A small learning app with four **real CrewAI agents** and a Streamlit UI. The crew reviews the dummy Shopflow API in the sibling `shopflow` folder and creates an evidence-backed release-readiness report.

## Run it

Use Python 3.11–3.13 and `uv`. First, start the existing Shopflow API in one PowerShell terminal:

```powershell
cd "C:\Salmon\Edureka\AI agents\Advanced_agents\shopflow"
uv run uvicorn backend.main:app --reload --port 8001
```

In another terminal:

```powershell
cd "C:\Salmon\Edureka\AI agents\Advanced_agents\release_readiness_crewAI"
uv sync
uv run streamlit run streamlit_app.py --server.port 8504
```

Open <http://localhost:8504>. Select a release and environment, then click **Run readiness review**. Review the manager report, three specialist assessments, deployment gates, captured API responses, and delegation activity. Download the validated report as Markdown or JSON.

The app reads `C:\Salmon\Edureka\AI agents\.env`. Keep your real key there:

```dotenv
OPEN_ROUTER_API_KEY=your_key_here
READINESS_MODEL=openai/gpt-oss-20b
SHOPFLOW_API_URL=http://127.0.0.1:8001
```

`OPENROUTER_API_KEY` is also accepted. `READINESS_MODEL` falls back to `OPENROUTER_MODEL`, then `openai/gpt-oss-20b`. The key is read again on every review submission, so rotating it does not require restarting the app. File values take precedence over matching process environment variables. Never commit the shared `.env`.

Model calls start only on submission. Refreshing the page, refreshing the release list, and downloading a report do not start another review. Each review makes multiple OpenRouter calls and may take a few minutes.

## How the four agents work

```text
Release manager (CrewAI hierarchical manager)
    delegates QA review ----------> QA engineer
    delegates security review ----> Security analyst
    delegates documentation ------> Documentation specialist
    receives all three assessments
    consolidates the release-readiness report
```

| Agent | Evidence tool | Responsibility |
| --- | --- | --- |
| Release manager | CrewAI delegation tools | Assign specialist reviews, consolidate findings, recommend release, assign follow-up actions |
| QA engineer | `read_qa_evidence` | Passed, failed, skipped tests; unresolved bugs and severity |
| Security analyst | `read_security_evidence` | Scan results, open vulnerabilities, policy risk |
| Documentation specialist | `read_documentation_evidence` | Required documents, approvals, release-note completeness |

The three review tasks run in order to make the example easy to follow. CrewAI's `Process.hierarchical` executes them through the manager, which delegates to the assigned coworker. The final task receives all three structured assessments as context. The manager has no direct evidence tool, and specialists cannot delegate.

Specialist outputs use the `SpecialistAssessment` Pydantic model. The manager produces a `ManagerReport` with a recommendation, executive summary, blockers, advisory risks, actions with owners, and source references. The final exported `ReadinessReport` adds the captured evidence, deployment rules, gate results, timestamps, and activity.

## Recommendations follow Shopflow policy

| Result | Meaning |
| --- | --- |
| `GO` | Every gate passes and the API returns no policy warnings |
| `GO_WITH_WARNINGS` | Every gate passes, with permitted gaps reported by the API |
| `NO_GO` | At least one deployment gate fails |
| `INCOMPLETE` | The review failed, specialist evidence is missing, output validation failed, or evidence changed during the review |

The agents explain the evidence; the API's deployment rules determine the recommendation. Every failed gate must have a blocker citation. Each specialist must actually be delegated to and read its own evidence tool. Unsupported evidence references and incorrect scope are rejected. A task gets one guardrail retry to correct its output; a still-invalid review has no report download.

With unmodified Shopflow seed data:

| Release | Staging | Production |
| --- | --- | --- |
| 1.0.0 (`rel-100`) | GO | GO |
| 1.1.0 (`rel-110`) | GO_WITH_WARNINGS | NO_GO |
| 1.2.0 (`rel-120`) | NO_GO | NO_GO |

Use the existing Shopflow simulator at <http://localhost:8503> to change dummy records, then submit a new review. Its separate launch command is `uv run streamlit run streamlit_app.py --server.port 8503` from the `shopflow` folder. Do not change records during a review: the app rereads all captured evidence before publishing and rejects a report if anything changed.

## Read the code in this order

| File | What to learn |
| --- | --- |
| `streamlit_app.py` | Select inputs, submit a review, display results, download reports |
| `release_crew/runner.py` | One complete review lifecycle and error handling |
| `release_crew/agents.py` | Four agent roles and their allowed tools |
| `release_crew/tasks.py` | Delegated tasks and manager consolidation |
| `release_crew/crew.py` | Hierarchical CrewAI configuration |
| `release_crew/tools.py` | Three narrow, read-only evidence tools |
| `release_crew/client.py` | HTTP calls to Shopflow |
| `release_crew/evidence.py` | Per-review evidence capture and freshness check |
| `release_crew/models.py` | Structured inputs, assessments, and reports |
| `release_crew/validation.py` | Scope, policy, provenance, and coverage guardrails |
| `release_crew/reporting.py` | Markdown generation from the validated report |
| `release_crew/config.py` | Shared `.env` configuration |

`activity.py` records tool/delegation events through a scoped CrewAI hook. `context.py` gives guardrails access to the current review without global shared review state. `llm.py` uses CrewAI's native OpenAI-compatible client for OpenRouter and estimates context length locally with tiktoken. `crew.py` keeps CrewAI task logs in memory for independent review sessions.

The app reads release metadata, tests, test summaries, bugs, security reports, documents, release notes, and deployment rules. Its only POST is the existing **non-mutating** `/releases/{id}/deployment-check`. It does not edit Shopflow evidence, execute deployments, run security scans, or perform fixes. Reports live in the Streamlit session until downloaded. Crew memory, tracing, planning, and shared caching are disabled.

## Checks and troubleshooting

```powershell
uv run pytest -q
uvx ty check --python .venv\Scripts\python.exe release_crew streamlit_app.py tests
```

Tests run offline with captured dummy API fixtures and a scripted LLM. They exercise the actual CrewAI hierarchy, deployment recommendations, delegation, source checks, changed evidence, provider failures, report exports, and UI reruns. CrewAI 1.15.44 currently emits internal deprecation warnings during framework tests; these do not fail the tests.

- **API unavailable:** start Shopflow on port 8001 and check the sidebar URL.
- **INCOMPLETE:** check the activity and any completed partial assessments. Verify OpenRouter key, credits, model access, and connectivity, then submit again.
- **Updated key:** save it in the shared `.env`, then click Run again. No key is shown in the UI or reports.
- **Unexpected recommendation:** inspect the selected environment's seven gate results. Staging and production intentionally apply different requirements.

The lockfile pins the tested dependency versions. This example uses CrewAI's native hierarchical manager API, described in the [custom manager guide](https://docs.crewai.com/en/learn/custom-manager-agent) and [hierarchical process guide](https://docs.crewai.com/en/learn/hierarchical-process).
