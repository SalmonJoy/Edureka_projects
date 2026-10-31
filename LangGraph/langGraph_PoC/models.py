"""Validated data contracts and the shared LangGraph state."""

from datetime import date, datetime
from typing import Annotated, Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator


NonBlankString = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]


class ExtractedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date | None = None
    end_date: date | None = None
    stated_days: int | None = Field(default=None, strict=True)
    leave_type: str | None = None
    reason: str | None = None
    clarification_question: str | None = None


class Employee(BaseModel):
    employee_id: NonBlankString
    name: NonBlankString
    email: NonBlankString
    department: NonBlankString
    designation: NonBlankString
    joining_date: date
    manager_id: NonBlankString | None
    leave_balance: int = Field(strict=True, ge=0)


class LeaveDetails(BaseModel):
    start_date: date
    end_date: date
    leave_type: NonBlankString
    reason: NonBlankString

    @model_validator(mode="after")
    def ordered_dates(self) -> "LeaveDetails":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class PolicyChunk(BaseModel):
    chunk_id: NonBlankString
    title: NonBlankString
    content: NonBlankString


class PolicyResponse(BaseModel):
    query: str
    chunks: list[PolicyChunk] = Field(min_length=1)


class EligibilityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: StrictBool
    policy_supported: StrictBool
    reasons: list[NonBlankString] = Field(min_length=1)
    supporting_chunk_ids: list[NonBlankString]


class ApplicationRecord(BaseModel):
    application_id: UUID
    submitted_at: datetime
    employee: Employee
    leave: LeaveDetails

    @model_validator(mode="after")
    def timezone_required(self) -> "ApplicationRecord":
        if self.submitted_at.utcoffset() is None:
            raise ValueError("submitted_at must include its timezone")
        return self


class Clarification(TypedDict):
    question: str
    answer: str


class LeaveState(TypedDict, total=False):
    # All values stored here are JSON-serializable; clients and keys stay outside state.
    original_request: str
    current_date: str
    clarification_history: list[Clarification]
    clarification_question: str | None
    extracted_request: dict
    leave: dict
    requested_days: int
    employee: dict | None
    employee_error: str | None
    policy_chunks: list[dict] | None
    policy_error: str | None
    eligibility: dict
    confirmed: bool
    confirmation_error: str | None
    application: dict
    error: str | None
    final_message: str
    status: Literal[
        "understanding", "clarifying", "ready", "awaiting_confirmation",
        "confirmed", "cancelled", "ineligible", "submitted", "error",
        "submission_unknown",
    ]
