import json
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

from chat_agent.agent import ChatAgent
from chat_agent.main import chat_loop
from chat_agent.model import ModelError


def answer(text):
    return {"role": "assistant", "content": text}


def calls(*items):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"function": {"name": name, "arguments": arguments}}
            for name, arguments in items
        ],
    }


class FakeModel:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    async def chat(self, messages, tools):
        self.requests.append(deepcopy(messages))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return deepcopy(reply)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def make_tools(self, *results):
        tools = Mock()
        tools.definitions = []
        tools.call = AsyncMock(side_effect=results)
        return tools

    async def test_ordinary_answers_and_follow_up_history(self):
        model = FakeModel(answer("Hello."), answer("You said hello."))
        tools = self.make_tools()
        agent = ChatAgent(model, tools)
        self.assertEqual(await agent.ask("Hello"), "Hello.")
        self.assertEqual(await agent.ask("What did I say?"), "You said hello.")
        self.assertEqual(
            [item["role"] for item in model.requests[1]],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(model.requests[1][1]["content"], "Hello")
        tools.call.assert_not_awaited()

    async def test_multiple_tool_calls_feed_results_back_to_model(self):
        model = FakeModel(
            calls(
                ("get_employee", {"employee_id": "EMP001"}),
                ("search_policies", {"query": "annual leave"}),
            ),
            answer("Employee has 20 days of annual leave."),
        )
        outputs = [
            {"is_error": False, "result": {"leave_balance": 20}},
            {"is_error": False, "result": {"chunks": [{"title": "Leave policy"}]}},
        ]
        tools = self.make_tools(*outputs)
        on_call = Mock()
        agent = ChatAgent(model, tools, on_tool_call=on_call)
        self.assertIn("20 days", await agent.ask("Look up EMP001 and the leave policy"))
        self.assertEqual(tools.call.await_count, 2)
        self.assertEqual(on_call.call_count, 2)
        results = model.requests[1][-2:]
        self.assertEqual([item["role"] for item in results], ["tool", "tool"])
        self.assertEqual([json.loads(item["content"]) for item in results], outputs)

    async def test_employee_then_leave_preserves_full_employee(self):
        employee = {
            "employee_id": "EMP001",
            "name": "Example Employee",
            "email": "employee@example.com",
            "department": "Engineering",
            "designation": "Developer",
            "joining_date": "2024-01-15",
            "manager_id": None,
            "leave_balance": 20,
        }
        leave = {
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
            "leave_type": "annual",
            "reason": "Personal time",
        }
        arguments = {"employee": employee, "leave": leave}
        model = FakeModel(
            calls(("get_employee", {"employee_id": "EMP001"})),
            calls(("apply_leave", arguments)),
            answer("Your application was recorded, not approved."),
        )
        tools = self.make_tools(
            {"is_error": False, "result": employee},
            {"is_error": False, "result": {"application_id": "example-id"}},
        )
        agent = ChatAgent(model, tools)
        self.assertIn("not approved", await agent.ask("Submit this leave request"))
        tools.call.assert_any_await("apply_leave", arguments)
        self.assertEqual(json.loads(model.requests[1][-1]["content"])["result"], employee)

    async def test_tool_error_reaches_model_and_user(self):
        model = FakeModel(
            calls(("get_employee", {"employee_id": "UNKNOWN"})),
            answer("That employee was not found."),
        )
        tools = self.make_tools({"is_error": True, "result": "Employee not found"})
        agent = ChatAgent(model, tools)
        self.assertIn("not found", await agent.ask("Look up UNKNOWN"))
        self.assertTrue(json.loads(model.requests[1][-1]["content"])["is_error"])

    async def test_model_failure_keeps_history_and_does_not_retry(self):
        model = FakeModel(ModelError("Ollama is unavailable."), answer("Hello again."))
        agent = ChatAgent(model, self.make_tools())
        self.assertEqual(await agent.ask("Hello"), "Ollama is unavailable.")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(await agent.ask("Try a new question"), "Hello again.")

    async def test_empty_answer_has_a_readable_fallback(self):
        agent = ChatAgent(FakeModel(answer("")), self.make_tools())
        self.assertIn("empty answer", await agent.ask("Hello"))

    async def test_turn_limit_stops_and_retains_completed_tool_results(self):
        model = FakeModel(calls(("search_policies", {"query": "leave"})))
        tools = self.make_tools({"is_error": False, "result": {"chunks": []}})
        agent = ChatAgent(model, tools, max_model_requests=1)
        self.assertIn("Stopped after 1 model requests", await agent.ask("Find policies"))
        self.assertEqual(tools.call.await_count, 1)
        self.assertEqual(agent.messages[-2]["role"], "tool")
        self.assertEqual(agent.messages[-1]["role"], "assistant")

    async def test_identical_leave_call_is_not_executed_twice(self):
        arguments = {"employee": {"employee_id": "EMP001"}, "leave": {"reason": "test"}}
        for is_error in (False, True):
            with self.subTest(is_error=is_error):
                model = FakeModel(
                    calls(("apply_leave", arguments)),
                    calls(("apply_leave", arguments)),
                    answer("Submission result received."),
                )
                result = {"is_error": is_error, "result": "First submission result"}
                tools = self.make_tools(result)
                agent = ChatAgent(model, tools)
                await agent.ask("Submit leave")
                tools.call.assert_awaited_once_with("apply_leave", arguments)
                tool_messages = [item for item in agent.messages if item["role"] == "tool"]
                self.assertEqual(tool_messages[0]["content"], tool_messages[1]["content"])


class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_input_and_quit(self):
        agent = Mock(ask=AsyncMock(return_value="Hello."))
        read_input = Mock(side_effect=["   ", "Hello", " QUIT "])
        output = Mock()
        await chat_loop(agent, read_input, output)
        agent.ask.assert_awaited_once_with("Hello")
        output.assert_any_call("\nAssistant: Hello.")
        output.assert_any_call("Goodbye.")

    async def test_eof_exits_without_a_model_request(self):
        agent = Mock(ask=AsyncMock())
        output = Mock()
        await chat_loop(agent, Mock(side_effect=EOFError), output)
        agent.ask.assert_not_awaited()
        output.assert_called_once_with("Goodbye.")
