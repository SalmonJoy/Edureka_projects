"""Store dummy leave applications as JSON Lines for LangGraph exercises."""

from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints, model_validator


app = FastAPI(
    title="Leave Application API",
    description="Record leave applications in a local JSONL file for learning exercises.",
    version="1.0.0",
)

LEAVE_APPLICATIONS_FILE = Path(__file__).resolve().parent / "leave_applications.jsonl"
FILE_LOCK = Lock()

NonBlankString = Annotated[
    str, StringConstraints(strict=True, min_length=1, pattern=r"\S")
]


class Employee(BaseModel):
    employee_id: NonBlankString
    name: NonBlankString
    email: NonBlankString
    department: NonBlankString
    designation: NonBlankString
    joining_date: date
    manager_id: NonBlankString | None
    leave_balance: int = Field(strict=True, ge=0, description="Available leave in whole days")


class LeaveDetails(BaseModel):
    start_date: date
    end_date: date
    leave_type: NonBlankString
    reason: NonBlankString

    @model_validator(mode="after")
    def validate_date_range(self) -> "LeaveDetails":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class LeaveApplicationRequest(BaseModel):
    employee: Employee
    leave: LeaveDetails


class LeaveApplicationRecord(LeaveApplicationRequest):
    application_id: UUID
    submitted_at: datetime


@app.post(
    "/leave-applications",
    status_code=status.HTTP_201_CREATED,
    response_model=LeaveApplicationRecord,
    responses={500: {"description": "Could not save leave application"}},
)
def apply_leave(request: LeaveApplicationRequest) -> LeaveApplicationRecord:
    """Append an application without approving it or changing leave balances."""
    record = LeaveApplicationRecord(
        application_id=uuid4(),
        submitted_at=datetime.now(timezone.utc),
        employee=request.employee,
        leave=request.leave,
    )
    json_line = record.model_dump_json() + "\n"

    try:
        # This lock coordinates concurrent requests in a single app process.
        with FILE_LOCK:
            with LEAVE_APPLICATIONS_FILE.open("a", encoding="utf-8", newline="\n") as file:
                file.write(json_line)
    except OSError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save leave application",
        ) from error

    return record
