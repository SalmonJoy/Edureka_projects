"""Leave Application API tool (port 8002)."""

from typing import Any

from MCP_server.api_client import request_json
from MCP_server.config import LEAVE_API_URL
from MCP_server.models import Employee, LeaveDetails


async def apply_leave(employee: Employee, leave: LeaveDetails) -> dict[str, Any]:
    """Record a leave application using the full employee from get_employee.

    Does not approve leave or change balances. Repeated calls create separate
    applications; do not automatically retry after a timeout.
    """
    return await request_json(
        "POST",
        f"{LEAVE_API_URL}/leave-applications",
        # JSON mode converts Python date objects to ISO date strings.
        json_body={
            "employee": employee.model_dump(mode="json"),
            "leave": leave.model_dump(mode="json"),
        },
    )
