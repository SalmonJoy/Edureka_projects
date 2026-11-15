"""Exercise the MCP boundary and HTTP requests without calling live APIs."""

import asyncio
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import httpx
from mcp import Client

from MCP_server.config import EMPLOYEE_API_URL, LEAVE_API_URL, POLICY_API_URL, REQUEST_TIMEOUT
from MCP_server.server import mcp

EMPLOYEE = {
    "employee_id": "EMP001",
    "name": "Example Employee",
    "email": "employee@example.com",
    "department": "Engineering",
    "designation": "Developer",
    "joining_date": "2024-01-15",
    "manager_id": None,
    "leave_balance": 12,
}
LEAVE = {
    "start_date": "2026-12-01",
    "end_date": "2026-12-02",
    "leave_type": "annual",
    "reason": "Personal time",
}


def error_text(result):
    return " ".join(item.text for item in result.content if item.type == "text")


class ToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.handler = lambda request: httpx.Response(200, json={})
        original_client = httpx.AsyncClient

        def handle_request(request):
            self.requests.append(request)
            return self.handler(request)

        def make_client(**kwargs):
            return original_client(
                transport=httpx.MockTransport(handle_request), **kwargs
            )

        # Every upstream request is intercepted, including unexpected requests.
        self.client_mock = self.enterContext(
            patch("MCP_server.api_client.httpx.AsyncClient", side_effect=make_client)
        )

    async def test_discovery_exposes_four_typed_tools(self):
        async with Client(mcp) as client:
            listing = await client.list_tools()

        tools = {tool.name: tool for tool in listing.tools}
        self.assertEqual(
            set(tools), {"get_employee", "get_employees", "search_policies", "apply_leave"}
        )
        self.assertEqual(
            tools["get_employee"].input_schema["required"], ["employee_id"]
        )
        self.assertEqual(tools["search_policies"].input_schema["required"], ["query"])
        batch_schema = tools["get_employees"].input_schema
        self.assertEqual(batch_schema["required"], ["employee_ids"])
        alternatives = batch_schema["properties"]["employee_ids"]["anyOf"]
        self.assertIn({"const": -1, "type": "integer"}, alternatives)
        array_schema = next(item for item in alternatives if item.get("type") == "array")
        self.assertEqual(array_schema["items"]["type"], "string")
        self.assertEqual(array_schema["minItems"], 1)
        schema = tools["apply_leave"].input_schema
        self.assertEqual(set(schema["required"]), {"employee", "leave"})
        employee_schema = schema["$defs"]["Employee"]
        self.assertEqual(set(employee_schema["required"]), set(EMPLOYEE))
        self.assertIn(
            {"type": "null"}, employee_schema["properties"]["manager_id"]["anyOf"]
        )
        leave_schema = schema["$defs"]["LeaveDetails"]
        self.assertEqual(set(leave_schema["required"]), set(LEAVE))
        self.assertEqual(leave_schema["properties"]["start_date"]["format"], "date")
        self.assertTrue(tools["get_employee"].annotations.read_only_hint)
        self.assertTrue(tools["get_employees"].annotations.read_only_hint)
        self.assertTrue(tools["search_policies"].annotations.read_only_hint)
        self.assertFalse(tools["apply_leave"].annotations.idempotent_hint)
        self.assertEqual(self.requests, [])

    async def test_employee_get_preserves_case_and_response(self):
        self.handler = lambda request: httpx.Response(200, json=EMPLOYEE)
        async with Client(mcp) as client:
            result = await client.call_tool("get_employee", {"employee_id": "emp001"})

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, EMPLOYEE)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url), f"{EMPLOYEE_API_URL}/employees/emp001")
        self.assertEqual(request.content, b"")
        self.client_mock.assert_called_once_with(timeout=REQUEST_TIMEOUT)

    async def test_batch_preserves_order_duplicates_and_case(self):
        ids = ["EMP003", "emp001", "EMP003"]
        self.handler = lambda request: httpx.Response(
            200, json={**EMPLOYEE, "employee_id": request.url.path.rsplit("/", 1)[-1]}
        )
        async with Client(mcp) as client:
            result = await client.call_tool("get_employees", {"employee_ids": ids})

        self.assertFalse(result.is_error)
        rows = result.structured_content["results"]
        self.assertEqual([row["employee_id"] for row in rows], ids)
        self.assertEqual([row["employee"]["employee_id"] for row in rows], ids)
        self.assertTrue(all(row["error"] is None for row in rows))
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(
            [str(request.url) for request in self.requests],
            [f"{EMPLOYEE_API_URL}/employees/{employee_id}" for employee_id in ids],
        )
        self.assertTrue(all(request.method == "GET" for request in self.requests))

    async def test_minus_one_fetches_all_ten_employees(self):
        self.handler = lambda request: httpx.Response(
            200, json={**EMPLOYEE, "employee_id": request.url.path.rsplit("/", 1)[-1]}
        )
        async with Client(mcp) as client:
            result = await client.call_tool("get_employees", {"employee_ids": -1})

        self.assertFalse(result.is_error)
        expected = [f"EMP{number:03d}" for number in range(1, 11)]
        rows = result.structured_content["results"]
        self.assertEqual([row["employee_id"] for row in rows], expected)
        self.assertEqual([row["employee"]["employee_id"] for row in rows], expected)
        self.assertEqual(len(self.requests), 10)

    async def test_batch_keeps_successes_when_other_lookups_fail(self):
        def respond(request):
            employee_id = request.url.path.rsplit("/", 1)[-1]
            if employee_id == "UNKNOWN":
                return httpx.Response(404, json={"detail": "Employee not found"})
            if employee_id == "SLOW":
                raise httpx.ReadTimeout("Too slow", request=request)
            if employee_id == "BAD_JSON":
                return httpx.Response(200, text="not JSON")
            return httpx.Response(200, json=EMPLOYEE)

        self.handler = respond
        ids = ["EMP001", "UNKNOWN", "SLOW", "BAD_JSON"]
        async with Client(mcp) as client:
            result = await client.call_tool("get_employees", {"employee_ids": ids})

        self.assertFalse(result.is_error)
        rows = result.structured_content["results"]
        self.assertEqual([row["employee_id"] for row in rows], ids)
        self.assertEqual(rows[0], {"employee_id": "EMP001", "employee": EMPLOYEE, "error": None})
        for row, message in zip(rows[1:], ["HTTP 404", "timed out", "invalid JSON"]):
            self.assertIsNone(row["employee"])
            self.assertIn(message, row["error"])
        self.assertEqual(len(self.requests), 4)

    async def test_all_failed_lookups_still_return_per_employee_errors(self):
        self.handler = lambda request: httpx.Response(500, text="Service unavailable")
        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_employees", {"employee_ids": ["EMP001", "EMP002"]}
            )
        self.assertFalse(result.is_error)
        rows = result.structured_content["results"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["employee"] is None for row in rows))
        self.assertTrue(all("HTTP 500" in row["error"] for row in rows))

    async def test_batch_lookups_run_concurrently_and_keep_result_order(self):
        second_started = asyncio.Event()
        completed = []

        async def respond(request):
            employee_id = request.url.path.rsplit("/", 1)[-1]
            if employee_id == "EMP001":
                await second_started.wait()
            else:
                second_started.set()
            completed.append(employee_id)
            return httpx.Response(200, json={**EMPLOYEE, "employee_id": employee_id})

        self.handler = respond
        async with Client(mcp) as client:
            result = await asyncio.wait_for(
                client.call_tool("get_employees", {"employee_ids": ["EMP001", "EMP002"]}),
                timeout=5,
            )
        self.assertFalse(result.is_error)
        self.assertEqual(completed, ["EMP002", "EMP001"])
        self.assertEqual(
            [row["employee_id"] for row in result.structured_content["results"]],
            ["EMP001", "EMP002"],
        )

    async def test_invalid_batch_inputs_are_rejected_before_http(self):
        cases = [{}, {"employee_ids": []}, {"employee_ids": "EMP001"},
                 {"employee_ids": "-1"}, {"employee_ids": 0},
                 {"employee_ids": [-1]}, {"employee_ids": None}]
        async with Client(mcp) as client:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    result = await client.call_tool("get_employees", arguments)
                    self.assertTrue(result.is_error)
                    self.assertIn("employee_ids", error_text(result))
        self.assertEqual(self.requests, [])

    async def test_policy_post_forwards_query_and_chunks(self):
        query = "What is the annual leave policy?"
        payload = {
            "query": query,
            "chunks": [
                {"chunk_id": "P1", "title": "Annual leave", "content": "Sample policy"}
            ],
        }
        self.handler = lambda request: httpx.Response(200, json=payload)
        async with Client(mcp) as client:
            result = await client.call_tool("search_policies", {"query": query})

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, payload)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), f"{POLICY_API_URL}/policies/search")
        self.assertEqual(json.loads(request.content), {"query": query})

    async def test_leave_post_serializes_dates_and_returns_created_record(self):
        payload = {
            "employee": EMPLOYEE,
            "leave": LEAVE,
            "application_id": "b1d07b28-de57-4a35-a91a-25f929b53a95",
            "submitted_at": "2026-11-15T10:00:00Z",
        }
        self.handler = lambda request: httpx.Response(201, json=payload)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "apply_leave", {"employee": EMPLOYEE, "leave": LEAVE}
            )

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, payload)
        # Only one call: apply_leave must not look up the employee itself.
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), f"{LEAVE_API_URL}/leave-applications")
        self.assertEqual(
            json.loads(request.content), {"employee": EMPLOYEE, "leave": LEAVE}
        )

    async def test_bad_inputs_are_rejected_before_http(self):
        missing_manager = deepcopy(EMPLOYEE)
        del missing_manager["manager_id"]
        missing_reason = deepcopy(LEAVE)
        del missing_reason["reason"]
        cases = [
            ("get_employee", {}, "employee_id"),
            ("search_policies", {"query": ""}, "query"),
            ("apply_leave", {"employee": missing_manager, "leave": LEAVE}, "manager_id"),
            ("apply_leave", {"employee": EMPLOYEE, "leave": missing_reason}, "reason"),
            (
                "apply_leave",
                {"employee": EMPLOYEE, "leave": {**LEAVE, "start_date": "2026-02-30"}},
                "start_date",
            ),
            (
                "apply_leave",
                {"employee": {**EMPLOYEE, "leave_balance": -1}, "leave": LEAVE},
                "leave_balance",
            ),
            (
                "apply_leave",
                {"employee": EMPLOYEE, "leave": {**LEAVE, "reason": "   "}},
                "reason",
            ),
        ]
        async with Client(mcp) as client:
            for tool, arguments, field in cases:
                with self.subTest(tool=tool, field=field):
                    result = await client.call_tool(tool, arguments)
                    self.assertTrue(result.is_error)
                    self.assertIn(field, error_text(result))
        self.assertEqual(self.requests, [])

    async def test_upstream_http_errors_are_tool_errors(self):
        cases = [
            ("get_employee", {"employee_id": "UNKNOWN"}, 404, "Employee not found"),
            ("search_policies", {"query": "leave"}, 422, "Invalid query"),
            (
                "apply_leave",
                {"employee": EMPLOYEE, "leave": LEAVE},
                500,
                "Could not save leave application",
            ),
        ]
        async with Client(mcp) as client:
            for tool, arguments, status, detail in cases:
                with self.subTest(status=status):
                    self.handler = lambda request: httpx.Response(
                        status, json={"detail": detail}
                    )
                    result = await client.call_tool(tool, arguments)
                    self.assertTrue(result.is_error)
                    self.assertIn(f"HTTP {status}", error_text(result))
                    self.assertIn(detail, error_text(result))
        self.assertEqual(len(self.requests), len(cases))

    async def test_unavailable_api_is_a_readable_tool_error(self):
        def unavailable(request):
            raise httpx.ConnectError("Connection refused", request=request)

        self.handler = unavailable
        async with Client(mcp) as client:
            result = await client.call_tool("get_employee", {"employee_id": "EMP001"})
        self.assertTrue(result.is_error)
        self.assertIn("Could not reach API", error_text(result))
        self.assertIn("Connection refused", error_text(result))

    async def test_leave_timeout_is_not_retried(self):
        def timeout(request):
            raise httpx.ReadTimeout("Too slow", request=request)

        self.handler = timeout
        async with Client(mcp) as client:
            result = await client.call_tool(
                "apply_leave", {"employee": EMPLOYEE, "leave": LEAVE}
            )
        self.assertTrue(result.is_error)
        self.assertIn("timed out after 10 seconds", error_text(result))
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_json_is_a_tool_error(self):
        self.handler = lambda request: httpx.Response(200, text="<html>not JSON</html>")
        async with Client(mcp) as client:
            result = await client.call_tool("get_employee", {"employee_id": "EMP001"})
        self.assertTrue(result.is_error)
        self.assertIn("invalid JSON", error_text(result))

    async def test_non_object_json_is_a_tool_error(self):
        self.handler = lambda request: httpx.Response(200, json=[])
        async with Client(mcp) as client:
            result = await client.call_tool("get_employee", {"employee_id": "EMP001"})
        self.assertTrue(result.is_error)
        self.assertIn("not an object", error_text(result))


if __name__ == "__main__":
    unittest.main()
