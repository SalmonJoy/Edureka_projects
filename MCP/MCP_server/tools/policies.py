"""HR Policy API tool (port 8001)."""

from typing import Annotated, Any

from pydantic import Field

from MCP_server.api_client import request_json
from MCP_server.config import POLICY_API_URL


async def search_policies(
    query: Annotated[str, Field(min_length=1, description="Your HR policy question")],
) -> dict[str, Any]:
    """Return five fixed fictional policy chunks for any query, without ranking."""
    return await request_json(
        "POST", f"{POLICY_API_URL}/policies/search", json_body={"query": query}
    )
