"""The HTTP plumbing shared by the API tools."""

from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from MCP_server.config import REQUEST_TIMEOUT


async def request_json(
    method: str, url: str, *, json_body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Make one API request and turn expected failures into MCP tool errors."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.request(method, url, json=json_body)
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ToolError(
            f"Request to {url} timed out after {REQUEST_TIMEOUT:g} seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            f"API at {url} returned HTTP {exc.response.status_code}: "
            f"{exc.response.text}"
        ) from exc
    except httpx.RequestError as exc:
        raise ToolError(f"Could not reach API at {url}: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise ToolError(f"API at {url} returned invalid JSON.") from exc

    if not isinstance(data, dict):
        raise ToolError(f"API at {url} returned JSON that is not an object.")
    return data
