"""List tools and call the read-only tools over Streamable HTTP."""

import asyncio
import json

from mcp import Client

from MCP_server.config import MCP_URL


def show_result(name, result):
    print(f"\n{name}:")
    if result.is_error:
        for content in result.content:
            if content.type == "text":
                print(content.text)
        raise RuntimeError(f"{name} failed; see the tool error above.")
    print(json.dumps(result.structured_content, indent=2, ensure_ascii=False))


async def main():
    async with Client(MCP_URL) as client:
        listing = await client.list_tools()
        print("Available tools:", ", ".join(tool.name for tool in listing.tools))

        employee = await client.call_tool("get_employee", {"employee_id": "EMP001"})
        show_result("get_employee", employee)

        employees = await client.call_tool(
            "get_employees", {"employee_ids": ["EMP001", "EMP003"]}
        )
        show_result("get_employees", employees)

        # To fetch the entire fictional dataset, use {"employee_ids": -1}.

        policies = await client.call_tool(
            "search_policies", {"query": "What is the annual leave policy?"}
        )
        show_result("search_policies", policies)


if __name__ == "__main__":
    asyncio.run(main())
