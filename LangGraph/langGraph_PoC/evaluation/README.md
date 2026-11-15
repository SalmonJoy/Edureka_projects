# Golden leave-agent dataset

`golden_leave_requests.jsonl` contains exactly **25** synthetic reference cases.
Each line has `inputs`, `reference_outputs`, and `metadata`, using the current
LangSmith SDK's `reference_outputs` field. The expected answers are authored from
the dummy policies, not copied from an agent run. Creating this dataset does not
measure the model's accuracy or execute an evaluation experiment.

Each example is self-contained: it includes its reference date in Asia/Kolkata,
the complete EMP004 fixture (12 leave days), and all five current HR policy
chunks. Most cases use **2026-11-07**; individual cases override the date to test
Monday-relative interpretation, same-day sick leave, and year rollover.

| ID | Coverage | Expected final state |
| --- | --- | --- |
| LEAVE001 | Explicit annual leave | Confirmation |
| LEAVE002 | Default annual leave type | Confirmation |
| LEAVE003 | Derive end date across a weekend | Confirmation |
| LEAVE004 | Inclusive weekday counting | Confirmation |
| LEAVE005 | One-day leave | Confirmation |
| LEAVE006 | Exactly 12 days of available balance | Confirmation |
| LEAVE007 | 13 days exceeds balance | Ineligible |
| LEAVE008 | Past start date | Ineligible |
| LEAVE009 | Weekend-only dates | Ineligible |
| LEAVE010 | Exactly three working days of notice | Confirmation |
| LEAVE011 | Insufficient notice | Ineligible |
| LEAVE012 | Relative next Monday with short notice | Ineligible |
| LEAVE013 | Next Monday when today is Monday | Confirmation |
| LEAVE014 | Same-day sick leave after manager notification | Confirmation |
| LEAVE015 | Omitted year rolls into January | Confirmation |
| LEAVE016 | Missing reason | Clarification |
| LEAVE017 | Missing start date | Clarification |
| LEAVE018 | Missing end date/duration | Clarification |
| LEAVE019 | Reversed dates | Clarification |
| LEAVE020 | Count conflicts with dates | Clarification |
| LEAVE021 | Nonpositive day count | Clarification |
| LEAVE022 | Clarification answer resumes extraction | Confirmation |
| LEAVE023 | Explicit Boolean approval | Submitted, mocked only |
| LEAVE024 | Explicit Boolean decline | Cancelled |
| LEAVE025 | History reuse, current dates override history, fixed employee, fresh confirmation | Confirmation |

## Evaluation contract

- Create a fresh graph/checkpointer and thread per case. Initialize with
  `initial_state(inputs["request"], today=date.fromisoformat(inputs["current_date"]))`.
  Populate `messages` using `convert_to_messages(inputs["conversation_history"])`
  from `langchain_core.messages`. Otherwise do not load live MongoDB history.
- Supply the example's employee and policy chunks through injected lookup clients.
  The LLM still performs extraction, policy assessment, and user-facing responses.
  Pin the clock and fixtures rather than replaying these historical dates through
  the FastAPI endpoint, which uses the real current date.
- Compare every listed `checkpoints` entry after the initial run and each resume.
  `resume_answers` specifies the interrupt kind and answer; confirmation values
  are strict JSON Booleans. For a graph use `Command(resume=answer)` with the same
  thread ID. An API adapter would need the actual current `prompt_id`.
- **Replace the leave-submission client with a recording mock.** Assert zero calls
  before confirmation, and exactly one after approval in LEAVE023. Never use the
  live leave API for that case. A mock receipt must contain a valid UUID, UTC
  timestamp, and matching employee/leave details; do not compare a fixed UUID.
- Match dates, type, weekday count, final status, interrupt kind, submission count,
  and receipt requirements structurally. The employee ID is a fixed configuration
  expectation; clarification-only cases have not fetched the employee yet.
- Assess reasons and clarification questions by meaning. `clarification_fields`
  describes information the question must request, not exact wording or an exact
  list of all questions. Accept reasonable reason paraphrases and additional
  relevant policy citations; `required_supporting_chunk_ids` is a required subset.
- `required_reason_codes` are **evaluation labels**, not fields currently emitted
  by the agent: `insufficient_balance`, `past_start_date`, `no_working_days`, and
  `insufficient_notice`. Check that the recorded reasons convey those meanings.
  Read actual eligibility from `state["eligibility"]["eligible"]`, and compare it
  with reference `eligible`. It remains unknown before clarification.
- For a submitted receipt require the exact API-returned ID and dates in the
  final response and the word "submitted". Never accept a claim of approval or
  balance deduction. Other cases must not claim a new application was submitted.

The golden interpretation treats the three-working-day notice statement in
HR001 as a requirement for planned annual leave. Notice excludes the current
date and includes the start date. Sick leave instead follows the notification
text; no medical certificate rule is invented. Manager approval comes later and
does not prevent submission. Weekends are excluded and holidays are ignored.
These labels evaluate the intended behavior; an LLM can fail them.

This set covers leave understanding, policy/balance decisions, memory use, and
human-review behavior. Transport failures, malformed LLM output, SQLite/MongoDB
recovery, and tracing internals remain covered by the existing automated tests.

## Validate or upload

From the parent `LangGraph` folder:

```powershell
.\langGraph_PoC\.venv\Scripts\python.exe -m unittest discover -s langGraph_PoC/evaluation/tests -v
.\langGraph_PoC\.venv\Scripts\python.exe -m langGraph_PoC.evaluation.upload_dataset
```

The uploader uses the shared LangSmith configuration and defaults to
`Edureka-Leave-Agent-Golden-v1`. It checks the data before writing and uses stable
example UUIDs per dataset/case. Repeating the command skips identical examples
and refuses to overwrite differing or unrelated examples. Use `--dataset-name`
for a deliberately new dataset version. Only dataset/example APIs are called;
the agent, Ollama, leave services, and databases are not executed.

After upload, `langsmith_dataset.json` contains the verified dataset link, ID,
example count, local file hash, and verification time. It contains no credentials.
The uploader verifies all 25 remote inputs and reference outputs against the
local data. The link opens the private dataset in your LangSmith workspace.
