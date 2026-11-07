"""Exercise the actual new SDK over a mocked HTTP transport; no live writes."""

from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

import httpx2
from langsmith import Client, NotFoundError, PermissionDeniedError

from langGraph_PoC.trace_inspection import inspect_run, query_workflow_runs


PROJECT = "01a1b407-8f7a-72e5-9ec1-de3ff72a0d68"
RUN = "01a1b417-65a1-7683-92a6-e0bda7189550"
NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)
RECORD = {"id": RUN, "project_id": PROJECT, "trace_id": RUN,
          "name": "Leave request", "run_type": "CHAIN", "status": "PENDING",
          "start_time": NOW.isoformat(), "is_root": True, "metadata": {"thread_id": "workflow-one"}}


class InspectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.responses = []
        requests, responses = self.requests, self.responses

        async def send(client, request, **kwargs):
            requests.append(request)
            status, body = responses.pop(0)
            return httpx2.Response(status, json=body, request=request)

        p = patch.object(httpx2.AsyncClient, "send", send)
        p.start()
        self.addCleanup(p.stop)
        self.client = Client(api_key="fake-test-key", api_url="https://test.invalid",
                             info={"version": "0.18.0"}, auto_batch_tracing=False)

    async def test_retrieve_and_url_use_only_v2_read_endpoints(self):
        self.responses.extend([(200, RECORD), (200, {"url": "https://apac.smith.langchain.com/test-trace"})])
        result = await inspect_run(self.client, project_id=PROJECT, run_id=RUN)
        self.assertEqual(result["run"]["run_type"], "CHAIN")
        self.assertEqual(result["url"], "https://apac.smith.langchain.com/test-trace")
        self.assertEqual([(r.method, r.url.path) for r in self.requests], [
            ("GET", "/api/v2/runs/" + RUN), ("GET", "/api/v2/runs/" + RUN + "/url")])
        self.assertEqual(self.requests[0].url.params["project_id"], PROJECT)
        self.assertIn("TRACE_ID", self.requests[0].url.params.get_list("selects"))
        self.assertEqual(self.requests[1].url.params["trace_id"], RUN)

    async def test_query_explicit_window_projection_and_cursor_pagination(self):
        self.responses.extend([(200, {"items": [RECORD], "next_cursor": "page-two"}),
                               (200, {"items": [], "next_cursor": None})])
        result = await query_workflow_runs(self.client, project_id=PROJECT, thread_id="workflow-one", min_start_time=NOW)
        self.assertEqual(len(result), 1)
        for request in self.requests:
            self.assertEqual((request.method, request.url.path), ("POST", "/api/v2/runs/query"))
            body = json.loads(request.content)
            self.assertEqual(body["project_ids"], [PROJECT])
            self.assertIn("min_start_time", body)
            self.assertIn("NAME", body["selects"])
            self.assertIn('"workflow-one"', body["filter"])
        self.assertEqual(json.loads(self.requests[1].content)["cursor"], "page-two")

    async def test_old_trace_lowercase_values_are_preserved(self):
        self.responses.extend([(200, {**RECORD, "run_type": "chain"}),
                               (200, {"url": "https://apac.smith.langchain.com/test-trace"})])
        result = await inspect_run(self.client, project_id=PROJECT, run_id=RUN)
        self.assertEqual(result["run"]["run_type"], "chain")

    async def test_no_implicit_time_window_or_blank_thread(self):
        for thread_id, when in [("workflow", NOW.replace(tzinfo=None)), (" ", NOW)]:
            with self.assertRaises(ValueError):
                await query_workflow_runs(self.client, project_id=PROJECT, thread_id=thread_id, min_start_time=when)
        self.assertEqual(self.requests, [])

    async def test_new_not_found_and_permission_exceptions(self):
        for status, error in [(404, NotFoundError), (403, PermissionDeniedError)]:
            self.responses.append((status, {"detail": "Unavailable"}))
            with self.assertRaises(error):
                await inspect_run(self.client, project_id=PROJECT, run_id=RUN)
        self.assertTrue(all(request.method == "GET" for request in self.requests))

    async def test_repeated_cursor_is_rejected(self):
        self.responses.extend([(200, {"items": [], "next_cursor": "repeat"}),
                               (200, {"items": [], "next_cursor": "repeat"})])
        with self.assertRaisesRegex(RuntimeError, "repeated"):
            await query_workflow_runs(self.client, project_id=PROJECT, thread_id="workflow", min_start_time=NOW)
        self.assertEqual(len(self.requests), 2)


if __name__ == "__main__":
    unittest.main()
