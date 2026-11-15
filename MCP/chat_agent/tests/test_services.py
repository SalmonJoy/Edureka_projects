import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from mcp.types import CallToolResult, TextContent, Tool

from chat_agent.config import Settings, load_settings
from chat_agent.mcp_tools import MCPTools
from chat_agent.model import ModelError, OllamaModel

SCHEMA = {
    "type": "object",
    "$defs": {
        "Employee": {
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        }
    },
    "properties": {"employee": {"$ref": "#/$defs/Employee"}},
    "required": ["employee"],
}
DEFINITION = {
    "type": "function",
    "function": {"name": "apply_leave", "description": "Record leave", "parameters": SCHEMA},
}


class ConfigTests(unittest.TestCase):
    def test_file_values_override_inherited_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "OLLAMA_API_KEY=file-key\nOLLAMA_HOST=https://example.com\n"
                "OLLAMA_MODEL=example-model\nMCP_PORT=8010\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"OLLAMA_API_KEY": "inherited-key"}, clear=True):
                settings = load_settings(env_file)
        self.assertEqual(settings.ollama_api_key, "file-key")
        self.assertEqual(settings.ollama_host, "https://example.com")
        self.assertEqual(settings.ollama_model, "example-model")
        self.assertEqual(settings.mcp_url, "http://127.0.0.1:8010/mcp")
        self.assertNotIn("file-key", repr(settings))

    def test_defaults_and_explicit_mcp_url(self):
        with patch.dict(os.environ, {"OLLAMA_API_KEY": "test-key"}, clear=True):
            settings = load_settings(Path("does-not-exist.env"))
            self.assertEqual(settings.ollama_host, "https://ollama.com")
            self.assertEqual(settings.ollama_model, "gpt-oss:20b")
            self.assertEqual(settings.mcp_url, "http://127.0.0.1:8003/mcp")
            os.environ["MCP_URL"] = "http://example.com/mcp"
            self.assertEqual(
                load_settings(Path("does-not-exist.env")).mcp_url,
                "http://example.com/mcp",
            )

    def test_missing_key_is_actionable(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Missing OLLAMA_API_KEY"):
                load_settings(Path("does-not-exist.env"))


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_preserves_nested_schema_and_handles_pagination(self):
        tool = Tool(name="apply_leave", description="Record leave", inputSchema=SCHEMA)
        client = Mock()
        client.list_tools = AsyncMock(
            side_effect=[
                SimpleNamespace(tools=[tool], next_cursor="page-two"),
                SimpleNamespace(tools=[], next_cursor=None),
            ]
        )
        tools = MCPTools(client)
        await tools.discover()
        self.assertEqual(tools.definitions, [DEFINITION])
        self.assertEqual(tools.names, {"apply_leave"})
        client.list_tools.assert_any_await(cursor="page-two")

    async def test_structured_results_and_text_errors(self):
        client = Mock()
        client.call_tool = AsyncMock(
            side_effect=[
                CallToolResult(content=[], structuredContent={"leave_balance": 20}),
                CallToolResult(
                    content=[TextContent(type="text", text="Employee not found")],
                    isError=True,
                ),
            ]
        )
        tools = MCPTools(client)
        tools.names = {"get_employee"}
        success = await tools.call("get_employee", {"employee_id": "EMP001"})
        failure = await tools.call("get_employee", {"employee_id": "UNKNOWN"})
        self.assertEqual(success, {"is_error": False, "result": {"leave_balance": 20}})
        self.assertEqual(failure, {"is_error": True, "result": "Employee not found"})

    async def test_unknown_tool_never_reaches_mcp(self):
        client = Mock(call_tool=AsyncMock())
        result = await MCPTools(client).call("unknown", {})
        self.assertTrue(result["is_error"])
        self.assertIn("Unknown tool", result["result"])
        client.call_tool.assert_not_awaited()

    async def test_submission_transport_failure_is_not_retried(self):
        client = Mock(call_tool=AsyncMock(side_effect=ConnectionError("Disconnected")))
        tools = MCPTools(client)
        tools.names = {"apply_leave"}
        result = await tools.call("apply_leave", {})
        self.assertTrue(result["is_error"])
        self.assertIn("may have been saved", result["result"])
        client.call_tool.assert_awaited_once()


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_schema_auth_history_and_message_are_preserved(self):
        requests = []
        message = {"role": "assistant", "content": "Hello", "thinking": "private"}

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"message": message})

        settings = Settings(ollama_api_key="test-key")
        http = httpx.AsyncClient(
            base_url="https://ollama.com/",
            headers={"Authorization": "Bearer test-key"},
            transport=httpx.MockTransport(respond),
        )
        with patch("chat_agent.model.httpx.AsyncClient", return_value=http) as constructor:
            async with OllamaModel(settings) as model:
                messages = [{"role": "user", "content": "Hello"}]
                result = await model.chat(messages, [DEFINITION])
        self.assertTrue(http.is_closed)
        constructor.assert_called_once_with(
            base_url="https://ollama.com/",
            headers={"Authorization": "Bearer test-key"},
            timeout=120.0,
        )
        self.assertEqual(str(requests[0].url), "https://ollama.com/api/chat")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer test-key")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["tools"], [DEFINITION])
        self.assertEqual(payload["messages"], messages)
        self.assertFalse(payload["stream"])
        self.assertEqual(result, message)

    async def test_http_and_malformed_response_errors(self):
        cases = [
            (401, {"error": "Unauthorized"}, "HTTP 401"),
            (402, {"error": "Payment required"}, "HTTP 402.*Payment Required"),
            (200, {"message": None}, "invalid chat response"),
            (200, {}, "invalid chat response"),
            (
                200,
                {"message": {"role": "assistant", "tool_calls": [{"function": {"name": "x", "arguments": "bad"}}]}},
                "invalid chat response",
            ),
        ]
        for status, payload, expected in cases:
            with self.subTest(status=status, expected=expected):
                http = httpx.AsyncClient(
                    base_url="https://ollama.com/",
                    transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
                )
                with patch("chat_agent.model.httpx.AsyncClient", return_value=http):
                    async with OllamaModel(Settings(ollama_api_key="test-key")) as model:
                        with self.assertRaisesRegex(ModelError, expected):
                            await model.chat([], [])

    async def test_timeout_and_connection_errors(self):
        for exception, expected in [
            (httpx.ReadTimeout, "timed out"),
            (httpx.ConnectError, "Could not reach Ollama"),
        ]:
            with self.subTest(exception=exception):
                def fail(request):
                    raise exception("Connection failed", request=request)

                http = httpx.AsyncClient(
                    base_url="https://ollama.com/", transport=httpx.MockTransport(fail)
                )
                with patch("chat_agent.model.httpx.AsyncClient", return_value=http):
                    async with OllamaModel(Settings(ollama_api_key="test-key")) as model:
                        with self.assertRaisesRegex(ModelError, expected):
                            await model.chat([], [])
