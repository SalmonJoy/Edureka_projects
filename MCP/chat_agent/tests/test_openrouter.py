import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from chat_agent.agent import ChatAgent
from chat_agent.config import Settings, load_settings
from chat_agent.model import ModelError
from chat_agent.openrouter_model import OpenRouterModel, to_openrouter_messages
from chat_agent.tests.test_services import DEFINITION


class OpenRouterConfigTests(unittest.TestCase):
    def test_user_key_name_selects_openrouter_automatically(self):
        with patch.dict(
            os.environ,
            {"OPEN_ROUTER_API_KEY": "router-test-key", "OLLAMA_API_KEY": "ollama-test-key"},
            clear=True,
        ):
            settings = load_settings(Path("does-not-exist.env"))
        self.assertEqual(settings.model_provider, "openrouter")
        self.assertEqual(settings.openrouter_api_key, "router-test-key")
        self.assertEqual(settings.openrouter_model, "openrouter/free")
        self.assertNotIn("router-test-key", repr(settings))
        self.assertNotIn("ollama-test-key", repr(settings))

    def test_standard_key_alias_and_explicit_ollama_selection(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "router-key"}, clear=True):
            self.assertEqual(
                load_settings(Path("does-not-exist.env")).model_provider, "openrouter"
            )
            os.environ["MODEL_PROVIDER"] = "ollama"
            with self.assertRaisesRegex(ValueError, "Missing OLLAMA_API_KEY"):
                load_settings(Path("does-not-exist.env"))

    def test_missing_openrouter_key_and_invalid_provider(self):
        with patch.dict(os.environ, {"MODEL_PROVIDER": "openrouter"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Missing OPEN_ROUTER_API_KEY"):
                load_settings(Path("does-not-exist.env"))
            os.environ["MODEL_PROVIDER"] = "unknown"
            with self.assertRaisesRegex(ValueError, "MODEL_PROVIDER must be"):
                load_settings(Path("does-not-exist.env"))


class OpenRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_keeps_schema_and_response_normalizes_arguments(self):
        requests = []
        assistant = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_employee",
                    "type": "function",
                    "function": {"name": "get_employee", "arguments": '{"employee_id":"EMP001"}'},
                }
            ],
        }

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"choices": [{"message": assistant}]})

        http = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1/",
            headers={"Authorization": "Bearer dummy-key"},
            transport=httpx.MockTransport(respond),
        )
        with patch("chat_agent.openrouter_model.httpx.AsyncClient", return_value=http):
            async with OpenRouterModel(Settings(openrouter_api_key="dummy-key")) as model:
                reply = await model.chat([{"role": "user", "content": "Find EMP001"}], [DEFINITION])

        self.assertTrue(http.is_closed)
        self.assertEqual(str(requests[0].url), "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer dummy-key")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["tools"], [DEFINITION])
        self.assertEqual(payload["model"], "openrouter/free")
        self.assertEqual(reply["content"], "")
        self.assertEqual(reply["tool_calls"][0]["id"], "call_employee")
        self.assertEqual(
            reply["tool_calls"][0]["function"]["arguments"], {"employee_id": "EMP001"}
        )

    async def test_tool_ids_survive_agent_loop_and_outbound_conversion(self):
        assistant = {
            "role": "assistant",
            "content": "",
            "reasoning_details": [{"type": "reasoning.text", "text": "provider detail"}],
            "tool_calls": [
                {"id": "call_1", "function": {"name": "get_employee", "arguments": {"employee_id": "EMP001"}}},
                {"id": "call_2", "function": {"name": "search_policies", "arguments": {"query": "leave"}}},
            ],
        }
        model = Mock(chat=AsyncMock(side_effect=[deepcopy(assistant), {"role": "assistant", "content": "Done"}]))
        tools = Mock(definitions=[], call=AsyncMock(return_value={"is_error": False, "result": {}}))
        agent = ChatAgent(model, tools)
        self.assertEqual(await agent.ask("Find employee and policies"), "Done")
        history = deepcopy(agent.messages)
        converted = to_openrouter_messages(history)
        self.assertEqual(history, agent.messages, "Conversion must not change conversation history")
        tool_results = [item for item in converted if item["role"] == "tool"]
        self.assertEqual([item["tool_call_id"] for item in tool_results], ["call_1", "call_2"])
        self.assertNotIn("tool_name", tool_results[0])
        call_message = converted[2]
        self.assertEqual(call_message["reasoning_details"], assistant["reasoning_details"])
        self.assertEqual(
            json.loads(call_message["tool_calls"][0]["function"]["arguments"]),
            {"employee_id": "EMP001"},
        )

    async def test_http_and_invalid_response_errors(self):
        cases = [
            (401, {"error": "invalid key"}, "HTTP 401"),
            (429, {"error": "rate limited"}, "HTTP 429"),
            (200, {"choices": []}, "invalid chat response"),
            (
                200,
                {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "x", "arguments": "not JSON"}}]}}]},
                "invalid chat response",
            ),
        ]
        for status, payload, expected in cases:
            with self.subTest(status=status):
                http = httpx.AsyncClient(
                    base_url="https://openrouter.ai/api/v1/",
                    transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
                )
                with patch("chat_agent.openrouter_model.httpx.AsyncClient", return_value=http):
                    async with OpenRouterModel(Settings(openrouter_api_key="dummy")) as model:
                        with self.assertRaisesRegex(ModelError, expected):
                            await model.chat([], [])
