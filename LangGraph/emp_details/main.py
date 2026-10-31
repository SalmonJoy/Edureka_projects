"""Dummy employee API for LangGraph learning exercises."""

from datetime import date

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(
    title="Employee Details API",
    description="Look up fictional employees for LangGraph learning exercises.",
    version="1.0.0",
)


class Employee(BaseModel):
    employee_id: str
    name: str
    email: str
    department: str
    designation: str
    joining_date: date
    manager_id: str | None
    leave_balance: int = Field(ge=0, description="Available leave in whole days")


# Placeholder data lives in code; no database is required.
EMPLOYEES: dict[str, dict[str, str | int | None]] = {
    "EMP001": {
        "employee_id": "EMP001",
        "name": "Ananya Sharma",
        "email": "ananya.sharma@example.com",
        "department": "Management",
        "designation": "General Manager",
        "joining_date": "2019-04-15",
        "manager_id": None,
        "leave_balance": 20,
    },
    "EMP002": {
        "employee_id": "EMP002",
        "name": "Rohan Mehta",
        "email": "rohan.mehta@example.com",
        "department": "Engineering",
        "designation": "Engineering Manager",
        "joining_date": "2020-01-20",
        "manager_id": "EMP001",
        "leave_balance": 15,
    },
    "EMP003": {
        "employee_id": "EMP003",
        "name": "Priya Nair",
        "email": "priya.nair@example.com",
        "department": "Human Resources",
        "designation": "HR Manager",
        "joining_date": "2020-07-06",
        "manager_id": "EMP001",
        "leave_balance": 18,
    },
    "EMP004": {
        "employee_id": "EMP004",
        "name": "Arjun Rao",
        "email": "arjun.rao@example.com",
        "department": "Engineering",
        "designation": "Software Engineer",
        "joining_date": "2022-03-14",
        "manager_id": "EMP002",
        "leave_balance": 12,
    },
    "EMP005": {
        "employee_id": "EMP005",
        "name": "Kavya Iyer",
        "email": "kavya.iyer@example.com",
        "department": "Engineering",
        "designation": "Data Engineer",
        "joining_date": "2022-08-01",
        "manager_id": "EMP002",
        "leave_balance": 9,
    },
    "EMP006": {
        "employee_id": "EMP006",
        "name": "Vikram Shah",
        "email": "vikram.shah@example.com",
        "department": "Finance",
        "designation": "Financial Analyst",
        "joining_date": "2021-11-22",
        "manager_id": "EMP001",
        "leave_balance": 16,
    },
    "EMP007": {
        "employee_id": "EMP007",
        "name": "Neha Kapoor",
        "email": "neha.kapoor@example.com",
        "department": "Marketing",
        "designation": "Marketing Specialist",
        "joining_date": "2023-02-13",
        "manager_id": "EMP001",
        "leave_balance": 7,
    },
    "EMP008": {
        "employee_id": "EMP008",
        "name": "Aditya Sen",
        "email": "aditya.sen@example.com",
        "department": "Human Resources",
        "designation": "HR Executive",
        "joining_date": "2023-06-05",
        "manager_id": "EMP003",
        "leave_balance": 10,
    },
    "EMP009": {
        "employee_id": "EMP009",
        "name": "Meera Joshi",
        "email": "meera.joshi@example.com",
        "department": "Engineering",
        "designation": "QA Engineer",
        "joining_date": "2024-01-08",
        "manager_id": "EMP002",
        "leave_balance": 0,
    },
    "EMP010": {
        "employee_id": "EMP010",
        "name": "Siddharth Das",
        "email": "siddharth.das@example.com",
        "department": "Operations",
        "designation": "Operations Coordinator",
        "joining_date": "2024-09-16",
        "manager_id": "EMP001",
        "leave_balance": 14,
    },
}


@app.get(
    "/employees/{employee_id}",
    response_model=Employee,
    responses={404: {"description": "Employee not found"}},
)
def get_employee(employee_id: str) -> dict[str, str | int | None]:
    """Return an employee using their case-sensitive ID (EMP001 to EMP010)."""
    employee = EMPLOYEES.get(employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee
