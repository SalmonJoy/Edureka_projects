"""Load and independently validate the 25 reference examples."""

from datetime import date, timedelta
import json
from pathlib import Path


DATA_FILE = Path(__file__).with_name("golden_leave_requests.jsonl")
DATASET_NAME = "Edureka-Leave-Agent-Golden-v1"


def weekdays(start: date, end: date) -> int:
    days = 0
    while start <= end:
        days += start.weekday() < 5
        start += timedelta(days=1)
    return days


def load_examples(path: Path = DATA_FILE) -> list[dict]:
    examples = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    validate_examples(examples)
    return examples


def validate_examples(examples: list[dict]):
    if len(examples) != 25:
        raise ValueError("The golden dataset must contain exactly 25 examples.")
    ids = [example["metadata"]["case_id"] for example in examples]
    if ids != [f"LEAVE{number:03d}" for number in range(1, 26)]:
        raise ValueError("Golden case IDs must be unique and ordered LEAVE001 through LEAVE025.")
    for example in examples:
        inputs, expected = example["inputs"], example["reference_outputs"]
        label = example["metadata"]["case_id"]
        if not inputs["request"].strip() or inputs["timezone"] != "Asia/Kolkata":
            raise ValueError(f"{label}: invalid request or timezone")
        today = date.fromisoformat(inputs["current_date"])
        if inputs["employee"]["employee_id"] != "EMP004" or inputs["employee"]["leave_balance"] != 12:
            raise ValueError(f"{label}: this dataset uses the frozen EMP004 / 12-day fixture")
        if [chunk["chunk_id"] for chunk in inputs["policy_chunks"]] != [f"HR{i:03d}" for i in range(1, 6)]:
            raise ValueError(f"{label}: all five policy chunks are required")
        checkpoints = expected["checkpoints"]
        answers = inputs["resume_answers"]
        if len(checkpoints) != len(answers) + 1:
            raise ValueError(f"{label}: every resume needs an expected checkpoint")
        for answer, checkpoint in zip(answers, checkpoints):
            if answer["kind"] != checkpoint["interrupt_kind"]:
                raise ValueError(f"{label}: resume kind does not match the pending interrupt")
            value = answer["answer"]
            if answer["kind"] == "confirmation":
                if type(value) is not bool:
                    raise ValueError(f"{label}: confirmation must be a strict Boolean")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}: clarification must be nonblank text")
        if expected["status"] != checkpoints[-1]["status"] or expected["interrupt_kind"] != checkpoints[-1]["interrupt_kind"]:
            raise ValueError(f"{label}: final status must match the last checkpoint")
        submission_count = expected["expected_submission_calls"]
        approved = any(item["kind"] == "confirmation" and item["answer"] is True for item in answers)
        if submission_count != int(expected["status"] == "submitted") or (submission_count and not approved):
            raise ValueError(f"{label}: a submission requires explicit approval")
        if expected["receipt_required"] != bool(submission_count):
            raise ValueError(f"{label}: receipt requirement must match submission")
        leave = expected["leave"]
        if leave is None:
            if expected["status"] != "clarifying" or not expected["clarification_fields"]:
                raise ValueError(f"{label}: missing leave details require clarification")
            if expected["eligible"] is not None:
                raise ValueError(f"{label}: eligibility is unknown before clarification")
            continue
        start, end = date.fromisoformat(leave["start_date"]), date.fromisoformat(leave["end_date"])
        count, notice = weekdays(start, end), weekdays(today + timedelta(days=1), start)
        if end < start or count != expected["requested_days"] or notice != expected["working_days_notice"]:
            raise ValueError(f"{label}: incorrect date order, weekday count, or notice")
        eligible = start >= today and 0 < count <= 12 and (leave["leave_type"] == "sick" or notice >= 3)
        if expected["eligible"] is not eligible:
            raise ValueError(f"{label}: eligibility contradicts the frozen policy interpretation")
        if not leave["reason"].strip():
            raise ValueError(f"{label}: a complete request requires a reason")
