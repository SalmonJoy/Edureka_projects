"""Discover MCP tools and adapt their schemas/results for the chat model."""

from typing import Any

from mcp import Client


class MCPTools:
    def __init__(self, client: Client):
        self.client = client
        self.definitions: list[dict[str, Any]] = []
        self.names: set[str] = set()

    async def discover(self) -> None:
        definitions = []
        cursor = None
        while True:
            listing = await self.client.list_tools(cursor=cursor)
            for tool in listing.tools:
                definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            # Preserve $defs and nested inputs from MCP.
                            "parameters": tool.input_schema,
                        },
                    }
                )
            cursor = listing.next_cursor
            if not cursor:
                break
        self.definitions = definitions
        self.names = {tool["function"]["name"] for tool in definitions}

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.names:
            return {"is_error": True, "result": f"Unknown tool: {name}"}
        try:
            result = await self.client.call_tool(name, arguments)
        except Exception:
            # SDK transport failures can include exception groups. Keep these
            # at the service boundary and never retry a submission here.
            message = "MCP call failed. Check that the MCP server is running."
            if name == "apply_leave":
                message += " The application may have been saved; do not retry automatically."
            return {"is_error": True, "result": message}

        text = "\n".join(item.text for item in result.content if item.type == "text")
        data = result.structured_content
        return {
            "is_error": result.is_error,
            "result": data if data is not None and not result.is_error else text,
        }
