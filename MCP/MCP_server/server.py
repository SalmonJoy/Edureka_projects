"""Create the MCP server and explicitly register its four API tools."""

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from MCP_server.config import MCP_HOST, MCP_PORT
from MCP_server.tools.employees import get_employee, get_employees
from MCP_server.tools.leave import apply_leave
from MCP_server.tools.policies import search_policies

mcp = MCPServer(
    "HR Learning Server",
    instructions=(
        "Use get_employees for multiple employee IDs, or pass -1 for all ten employees. "
        "Use get_employee to obtain the full employee record before apply_leave. "
        "search_policies returns fixed fictional policy chunks. "
        "apply_leave records an application without approving it or changing balances."
    ),
)

# tool() is a decorator. Applying it here keeps registration in one place and
# lets the individual tool modules stay independent of this server instance.

mcp.tool(annotations=ToolAnnotations(read_only_hint=True))(get_employee)
mcp.tool(annotations=ToolAnnotations(read_only_hint=True))(get_employees)
mcp.tool(annotations=ToolAnnotations(read_only_hint=True))(search_policies)
mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False
    )
)(apply_leave)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=MCP_HOST, port=MCP_PORT)
