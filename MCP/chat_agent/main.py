"""Run from the MCP directory: python -m chat_agent.main."""

import asyncio
import json

from mcp import Client

from chat_agent.agent import ChatAgent
from chat_agent.config import load_settings
from chat_agent.mcp_tools import MCPTools
from chat_agent.model import OllamaModel
from chat_agent.openrouter_model import OpenRouterModel


def show_tool_call(name, arguments):
    print(f"\n[tool] {name} {json.dumps(arguments, ensure_ascii=False)}")


async def chat_loop(agent, read_input=input, write_output=print):
    while True:
        try:
            # Keep the event loop free for the MCP connection while waiting.
            text = (await asyncio.to_thread(read_input, "\nYou: ")).strip()
        except EOFError:
            break
        if text.lower() in {"exit", "quit"}:
            break
        if text:
            write_output(f"\nAssistant: {await agent.ask(text)}")
    write_output("Goodbye.")


async def run() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        print(exc)
        return 1

    try:
        model_class = OpenRouterModel if settings.model_provider == "openrouter" else OllamaModel
        model_name = (
            settings.openrouter_model if settings.model_provider == "openrouter"
            else settings.ollama_model
        )
        async with (
            model_class(settings) as model,
            Client(settings.mcp_url, read_timeout_seconds=30) as client,
        ):
            tools = MCPTools(client)
            await tools.discover()
            print(f"Provider: {settings.model_provider}")
            print(f"Model: {model_name}")
            print(f"MCP: {settings.mcp_url}")
            print("Available tools:", ", ".join(sorted(tools.names)) or "(none)")
            print("Type a message, or exit / quit to stop.")
            await chat_loop(ChatAgent(model, tools, on_tool_call=show_tool_call))
    except Exception:
        print(
            "Chat agent could not start or lost a service connection. "
            "Check MCP_URL, the running MCP server, and model provider settings."
        )
        return 1
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        print("\nGoodbye.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
