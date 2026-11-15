"""Single and batch lookups using the Employee Details API (port 8000)."""

import asyncio
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from MCP_server.api_client import request_json
from MCP_server.config import EMPLOYEE_API_URL


async def get_employee(employee_id: str) -> dict[str, Any]:
    """Get a fictional employee by case-sensitive ID (EMP001 to EMP010)."""
    url = f"{EMPLOYEE_API_URL}/employees/{quote(employee_id, safe='')}"
    return await request_json("GET", url)


async def get_employees(
    employee_ids: Annotated[list[str], Field(min_length=1)] | Literal[-1],
) -> dict[str, Any]:
    """Get multiple employees by a non-empty list of case-sensitive IDs.

    Pass the integer -1 to fetch all ten fictional employees, EMP001 to EMP010.
    Results follow input order, including duplicates. Each entry contains the
    requested employee_id, an employee object or null, and an error or null.
    Individual lookup failures do not discard the other results.
    """
    ids = (
        [f"EMP{number:03d}" for number in range(1, 11)]
        if employee_ids == -1 else employee_ids
    )

    async def lookup(employee_id: str) -> dict[str, Any]:
        try:
            employee = await get_employee(employee_id)
        except ToolError as exc:
            return {"employee_id": employee_id, "employee": None, "error": str(exc)}
        return {"employee_id": employee_id, "employee": employee, "error": None}

    # gather runs the independent HTTP lookups concurrently while preserving
    # their original order in the returned list.
    results = await asyncio.gather(*(lookup(employee_id) for employee_id in ids))
    return {"results": results}
