"""Tracing tests never use the real .env or LangSmith services."""

import json
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import httpx
from langgraph.types import Command
from langsmith import Client, utils

from langGraph_PoC import tracing
from langGraph_PoC.tests.test_workflow import FakeClients, TODAY
from langGraph_PoC.workflow import build_graph, initial_state
from langGraph_PoC.workflow_persistence import thread_config
from langGraph_PoC.workflow_persistence import open_checkpointer
from langGraph_PoC.tests.test_workflow import extracted


class TracingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.env = Path(self.folder.name) / ".env"
        for name, value in [("_initialized", False), ("_client", None), ("_project", None), ("_project_id", None)]:
            p = patch.object(tracing, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"LANGSMITH_TRACING": "false"}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(utils.get_env_var.cache_clear)
        self.addCleanup(utils.get_tracer_project.cache_clear)
        self.http_patch = patch.object(tracing.httpx, "Client")
        self.http_factory = self.http_patch.start()
        self.addCleanup(self.http_patch.stop)
        self.http = self.http_factory.return_value.__enter__.return_value
        self.http.get.return_value = httpx.Response(200, json={"environments": [
            {"environment": "LOCAL", "tracer_session_name": "resolved-local"},
            {"environment": "PRODUCTION", "tracer_session_name": "resolved-production"},
        ]}, request=httpx.Request("GET", "https://example.com"))
        p = patch.object(tracing, "_SafeClient", return_value=MagicMock(spec=Client))
        self.sdk_factory = p.start()
        self.addCleanup(p.stop)
        self.sdk = self.sdk_factory.return_value

    def write_env(self, extra=""):
        self.env.write_text(
            "LANGSMITH_TRACING=true\nLANGSMITH_API_KEY=file-secret\n"
            "LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com\n"
            "LANGSMITH_AGENT_ID=leave-assistant\nLANGSMITH_AGENT_ENVIRONMENT=local\n"
            + extra, encoding="utf-8")

    def test_file_precedence_destination_workspace_and_idempotence(self):
        self.write_env("LANGSMITH_WORKSPACE_ID=workspace-one\n")
        os.environ.update(LANGSMITH_API_KEY="inherited-secret", LANGSMITH_PROJECT="wrong-project",
                          LANGSMITH_AGENT_ENVIRONMENT="production")
        self.assertTrue(tracing.configure_tracing(self.env))
        self.assertTrue(tracing.configure_tracing(self.env))
        self.http.get.assert_called_once()
        self.sdk_factory.assert_called_once()
        call = self.http.get.call_args
        self.assertEqual(call.args[0], "https://apac.api.smith.langchain.com/api/v1/agents/leave-assistant")
        self.assertEqual(call.kwargs["headers"], {"X-Api-Key": "file-secret", "X-Tenant-Id": "workspace-one"})
        self.assertEqual(self.sdk_factory.call_args.kwargs["session"].headers["X-Tenant-Id"], "workspace-one")
        self.assertEqual(tracing._project, "resolved-local")
        self.assertEqual(self.sdk_factory.call_args.kwargs["web_url"], "https://apac.smith.langchain.com")
        handler = tracing.tracing_config()["callbacks"][0]
        self.assertIs(handler.client, self.sdk)
        self.assertEqual(handler.project_name, "resolved-local")
        self.assertFalse(utils.tracing_is_enabled())  # only the verified explicit client is used

    def test_disabled_makes_no_network_calls_or_callbacks(self):
        self.write_env("LANGSMITH_TRACING=false\n")
        self.assertFalse(tracing.configure_tracing(self.env))
        self.assertEqual(tracing.tracing_config(), {})
        self.http.get.assert_not_called()
        self.sdk_factory.assert_not_called()

    def test_can_use_inherited_configuration_when_file_is_missing(self):
        os.environ.update(LANGSMITH_TRACING="true", LANGSMITH_API_KEY="inherited-secret",
                          LANGSMITH_AGENT_ID="leave-assistant", LANGSMITH_AGENT_ENVIRONMENT="local")
        self.assertTrue(tracing.configure_tracing(self.env))
        self.assertEqual(self.sdk_factory.call_args.kwargs["api_key"], "inherited-secret")

    def test_failure_is_sanitized_and_disables_inherited_tracing(self):
        self.write_env()
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        self.http.get.side_effect = RuntimeError("Never print file-secret or inherited-secret")
        with self.assertLogs(tracing.logger, "WARNING") as logs:
            self.assertFalse(tracing.configure_tracing(self.env))
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertIn("disabled", " ".join(logs.output))
        self.assertFalse(utils.tracing_is_enabled())
        self.assertEqual(tracing.tracing_config(), {})
        self.sdk_factory.assert_not_called()

    def test_invalid_environment_and_endpoint_do_not_send_credentials(self):
        self.write_env("LANGSMITH_ENDPOINT=http://example.com\n")
        with self.assertLogs(tracing.logger, "WARNING"):
            self.assertFalse(tracing.configure_tracing(self.env))
        self.http.get.assert_not_called()

    def test_missing_environment_does_not_fall_back_to_default_project(self):
        self.write_env("LANGSMITH_AGENT_ENVIRONMENT=staging\n")
        with self.assertLogs(tracing.logger, "WARNING"):
            self.assertFalse(tracing.configure_tracing(self.env))
        self.assertEqual(tracing.tracing_config(), {})
        self.sdk_factory.assert_not_called()

    def test_rejected_agent_lookup_disables_tracing(self):
        self.write_env()
        self.http.get.return_value = httpx.Response(403, request=httpx.Request("GET", "https://example.com"))
        with self.assertLogs(tracing.logger, "WARNING"):
            self.assertFalse(tracing.configure_tracing(self.env))
        self.sdk_factory.assert_not_called()

    async def test_initial_resume_and_checkpoint_updates_share_one_parent(self):
        self.write_env()
        self.assertTrue(tracing.configure_tracing(self.env))
        clients = FakeClients()
        graph = build_graph(clients)
        config = thread_config("trace-test-thread")
        state = initial_state("Three days for vacation", today=TODAY)
        async with tracing.workflow_trace(graph, config, state):
            paused = await graph.ainvoke(state, config)
            await graph.aupdate_state(config, {"messages": []})
        self.assertEqual(paused["__interrupt__"][0].value["kind"], "confirmation")
        self.assertEqual(clients.submissions, [])
        parent_id = state["trace_context"]["id"]
        parent_updates = [call for call in self.sdk.update_run.call_args_list
                          if str(call.args[0] if call.args else call.kwargs["run_id"]) == parent_id]
        self.assertEqual(parent_updates, [])
        config = thread_config("trace-test-thread")
        async with tracing.workflow_trace(graph, config):
            result = await graph.ainvoke(Command(resume=False), config)
        tracing.flush_traces()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(clients.submissions, [])
        calls = self.sdk.create_run.call_args_list
        names = [call.kwargs["name"] for call in calls]
        for node in ["understand_request", "fetch_employee", "fetch_policies", "check_eligibility", "human_review"]:
            self.assertIn(node, names)
        self.assertNotIn("apply_leave", names)
        for call in calls:
            self.assertEqual(call.kwargs["extra"]["metadata"]["thread_id"], "trace-test-thread")
        self.assertGreaterEqual(names.count("LangGraph"), 2)
        parents = [call for call in calls if call.kwargs["name"] == "Leave request"]
        self.assertEqual(len(parents), 1)
        for call in calls:
            self.assertEqual(str(call.kwargs["trace_id"]), parent_id)
            if call.kwargs["name"] != "Leave request":
                self.assertIsNotNone(call.kwargs.get("parent_run_id"))
        parent_updates = [call for call in self.sdk.update_run.call_args_list
                          if str(call.args[0] if call.args else call.kwargs["run_id"]) == parent_id]
        self.assertIsNotNone(parent_updates[-1].kwargs["end_time"])
        before = self.sdk.create_run.call_count, self.sdk.update_run.call_count
        config = thread_config("trace-test-thread")
        async with tracing.workflow_trace(graph, config):
            await graph.aupdate_state(config, {"messages": []})
        tracing.flush_traces()
        self.assertEqual(before, (self.sdk.create_run.call_count, self.sdk.update_run.call_count))

    async def test_clarification_trace_survives_sqlite_reopen_and_threads_are_isolated(self):
        self.write_env()
        tracing.configure_tracing(self.env)
        clients = FakeClients()
        clients.extractions = [extracted(reason=None), extracted()]
        path = Path(self.folder.name) / "checkpoints.sqlite"
        async with open_checkpointer(path) as saver:
            graph = build_graph(clients, checkpointer=saver)
            state = initial_state("Leave please", today=TODAY)
            config = thread_config("restart-thread")
            async with tracing.workflow_trace(graph, config, state):
                result = await graph.ainvoke(state, config)
            self.assertEqual(result["__interrupt__"][0].value["kind"], "clarification")
            saved_context = (await graph.aget_state(config)).values["trace_context"]
        async with open_checkpointer(path) as saver:
            graph = build_graph(clients, checkpointer=saver)
            config = thread_config("restart-thread")
            async with tracing.workflow_trace(graph, config):
                result = await graph.ainvoke(Command(resume="Family vacation"), config)
            self.assertEqual(result["__interrupt__"][0].value["kind"], "confirmation")
            self.assertEqual((await graph.aget_state(config)).values["trace_context"], saved_context)
            clients.extractions.append(extracted())
            other_state = initial_state("Other vacation", today=TODAY)
            other_config = thread_config("different-thread")
            async with tracing.workflow_trace(graph, other_config, other_state):
                await graph.ainvoke(other_state, other_config)
            self.assertNotEqual(other_state["trace_context"]["id"], saved_context["id"])
        tracing.flush_traces()
        parents = [c for c in self.sdk.create_run.call_args_list if c.kwargs["name"] == "Leave request"]
        self.assertEqual(len(parents), 2)
        self.assertEqual(clients.submissions, [])

    async def test_failed_parent_creation_continues_without_unparented_traces(self):
        self.write_env()
        tracing.configure_tracing(self.env)
        self.sdk.create_run.side_effect = RuntimeError("secret credentials")
        clients = FakeClients()
        graph = build_graph(clients)
        state = initial_state("Vacation", today=TODAY)
        config = thread_config("trace-failure")
        with self.assertLogs(tracing.logger, "WARNING") as logs:
            async with tracing.workflow_trace(graph, config, state):
                result = await graph.ainvoke(state, config)
        self.assertNotIn("secret", str(logs.output))
        self.assertEqual(result["__interrupt__"][0].value["kind"], "confirmation")
        self.assertEqual(self.sdk.create_run.call_count, 1)
        self.assertEqual(clients.submissions, [])

    async def test_terminal_clarification_and_confirmation_use_one_parent(self):
        from langGraph_PoC import main as cli

        self.write_env()
        tracing.configure_tracing(self.env)
        clients = FakeClients()
        clients.extractions = [extracted(reason=None), extracted()]
        graph = build_graph(clients)
        with patch("builtins.input", side_effect=["Family vacation", "no"]), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch.object(cli, "initial_state", side_effect=lambda text: initial_state(text, today=TODAY)):
            result = await cli.run_request(graph, "Leave please")
        tracing.flush_traces()
        self.assertEqual(result["status"], "cancelled")
        parents = [c for c in self.sdk.create_run.call_args_list if c.kwargs["name"] == "Leave request"]
        self.assertEqual(len(parents), 1)
        self.assertEqual(clients.submissions, [])


class RedactionTests(unittest.TestCase):
    def test_serialized_reprs_headers_errors_and_nested_reasoning_are_redacted(self):
        redact = tracing._redactor(["api-secret", "ollama-secret", "mongo-secret"])
        with patch.object(Client, "__init__", return_value=None), \
                patch.object(Client, "create_run") as create, patch.object(Client, "update_run") as update:
            client = tracing._SafeClient(redact=redact)
            client.create_run(name="model", inputs={"request": "Vacation"}, run_type="llm",
                              serialized={"repr": "Authorization Bearer ollama-secret"},
                              extra={"metadata": {"thread_id": "workflow-one", "api_key": "api-secret"}})
            client.update_run("run-one", error="mongo-secret failed", outputs={
                "content": "Eligible", "additional_kwargs": {"reasoning_content": "private reasoning"}})
            payload = json.dumps([create.call_args.kwargs, update.call_args.kwargs])
            for value in ["api-secret", "ollama-secret", "mongo-secret", "private reasoning"]:
                self.assertNotIn(value, payload)
            self.assertIn("workflow-one", payload)
            self.assertIn("Eligible", payload)


if __name__ == "__main__":
    unittest.main()
