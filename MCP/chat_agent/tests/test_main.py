import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from chat_agent.config import Settings
from chat_agent.main import run


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_exit_closes_both_service_connections(self):
        model_context = MagicMock()
        client_context = MagicMock()
        tools = MagicMock(names={"get_employee"})
        tools.discover = AsyncMock()
        with (
            patch("chat_agent.main.load_settings", return_value=Settings(ollama_api_key="dummy")),
            patch("chat_agent.main.OllamaModel", return_value=model_context),
            patch("chat_agent.main.Client", return_value=client_context),
            patch("chat_agent.main.MCPTools", return_value=tools),
            patch("chat_agent.main.chat_loop", new_callable=AsyncMock),
            patch("builtins.print"),
        ):
            self.assertEqual(await run(), 0)
        tools.discover.assert_awaited_once()
        model_context.__aexit__.assert_awaited_once()
        client_context.__aexit__.assert_awaited_once()

    async def test_unavailable_mcp_exits_cleanly_and_closes_model_client(self):
        model_context = MagicMock()
        client_context = MagicMock()
        client_context.__aenter__.side_effect = ConnectionError("Connection refused")
        with (
            patch("chat_agent.main.load_settings", return_value=Settings(ollama_api_key="dummy")),
            patch("chat_agent.main.OllamaModel", return_value=model_context),
            patch("chat_agent.main.Client", return_value=client_context),
            patch("builtins.print") as output,
        ):
            self.assertEqual(await run(), 1)
        self.assertIn("Check MCP_URL", output.call_args.args[0])
        model_context.__aexit__.assert_awaited_once()
