"""Typed inputs matching the leave API's OpenAPI schema."""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field

NonBlankString = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Employee(BaseModel):
    """Pass the complete object returned by get_employee."""

    employee_id: NonBlankString
    name: NonBlankString
    email: NonBlankString
    department: NonBlankString
    designation: NonBlankString
    joining_date: date
    # Required, but may be null (for an employee without a manager).
    manager_id: NonBlankString | None
    leave_balance: int = Field(ge=0, description="Available leave in whole days")


class LeaveDetails(BaseModel):
    start_date: date
    end_date: date
    leave_type: NonBlankString
    reason: NonBlankString
