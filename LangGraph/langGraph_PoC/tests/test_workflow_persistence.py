"""Real temporary SQLite files; all LLM, MongoDB, and leave API clients are fake."""

from contextlib import closing, redirect_stdout
import io
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import HumanMessage

from langGraph_PoC import main as cli
from langGraph_PoC.workflow import build_graph, initial_state
from langGraph_PoC.workflow_persistence import (
    CHECKPOINT_FILE, WorkflowPersistenceError, load_thread, open_checkpointer,
    saved_conversation_id, thread_config,
)
from langGraph_PoC.tests.test_conversation_memory import memory
from langGraph_PoC.tests.test_workflow import FakeClients, TODAY, extracted, saved_record


class WorkflowPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory(prefix="langgraph-sqlite-test-")
        self.path = Path(self.directory.name) / "workflow.sqlite"

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def run_cli(self, graph, *, request=None, store=None, thread=None, answers=()):
        with patch.object(cli, "initial_state", side_effect=lambda text: initial_state(text, today=TODAY)):
            with patch("builtins.input", side_effect=answers), redirect_stdout(io.StringIO()) as output:
                result = await cli.run_request(graph, request, memory=store, thread_id=thread)
        return result, output.getvalue()

    async def test_database_creation_location_and_missing_thread(self):
        self.assertEqual(CHECKPOINT_FILE.parent, Path(cli.__file__).resolve().parent)
        self.assertFalse(self.path.exists())
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(FakeClients(), checkpointer=saver)
            with self.assertRaisesRegex(WorkflowPersistenceError, "No saved workflow"):
                await load_thread(graph, "missing")
        self.assertTrue(self.path.is_file())
        with closing(sqlite3.connect(self.path)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"checkpoints", "writes"} <= tables)
        with self.assertRaisesRegex(WorkflowPersistenceError, "nonblank"):
            thread_config(" ")

    async def test_confirmation_restores_after_close_without_replaying_lookups(self):
        first, store = FakeClients(), memory()
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(first, checkpointer=saver)
            # Exit while waiting for input, after the review prompt was recorded.
            with self.assertRaises(EOFError):
                await self.run_cli(graph, request="Vacation", store=store, answers=[EOFError()])
            document = next(iter(store.collection.documents.values()))
            thread = document["thread_id"]
            snapshot = await load_thread(graph, thread)
            original_message_ids = [message.id for message in snapshot.values["messages"]]
            self.assertEqual(len(original_message_ids), 2)
        second = FakeClients()
        second.extractions = []
        await store.load()
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(second, checkpointer=saver)
            restored = await load_thread(graph, thread)
            self.assertEqual([message.id for message in restored.values["messages"]], original_message_ids)
            self.assertEqual(saved_conversation_id(restored), store.conversation_id)
            result, _ = await self.run_cli(graph, store=store, thread=thread, answers=["yes"])
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(second.events, [])
        self.assertEqual(second.assessments, [])
        self.assertEqual(len(second.submissions), 1)
        self.assertEqual(first.submissions, [])
        self.assertEqual(len(await store.load()), 4)

    async def test_clarification_resumes_after_reopen_then_declines_submission(self):
        first, store = FakeClients(), memory()
        first.extractions = [extracted(reason=None)]
        async with open_checkpointer(self.path) as saver:
            with self.assertRaises(EOFError):
                await self.run_cli(build_graph(first, checkpointer=saver), request="Leave", store=store, answers=[EOFError()])
            thread = next(iter(store.collection.documents.values()))["thread_id"]
        second = FakeClients()
        await store.load()
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(second, checkpointer=saver)
            final, _ = await self.run_cli(graph, store=store, thread=thread, answers=["Family vacation", "no"])
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(second.extraction_contexts[0]["clarification_history"][0]["answer"], "Family vacation")
        self.assertEqual(len(await store.load()), 6)
        self.assertEqual(second.submissions, [])

    async def test_completed_submission_is_displayed_without_another_post(self):
        first, store = FakeClients(), memory()
        async with open_checkpointer(self.path) as saver:
            result, _ = await self.run_cli(build_graph(first, checkpointer=saver), request="Vacation", store=store, answers=["yes"])
            thread = next(iter(store.collection.documents.values()))["thread_id"]
        second = FakeClients()
        second.extractions = []
        async with open_checkpointer(self.path) as saver:
            restored, output = await self.run_cli(build_graph(second, checkpointer=saver), store=store, thread=thread)
        self.assertIn(result["application"]["application_id"], output)
        self.assertEqual(restored["application"], result["application"])
        self.assertEqual(len(first.submissions), 1)
        self.assertEqual(second.submissions, [])
        self.assertEqual(second.success_calls, 0)
        self.assertEqual(len(await store.load()), 4)

    async def test_cancel_closes_pending_clarification_across_restart(self):
        first, store = FakeClients(), memory()
        first.extractions = [extracted(reason=None)]
        async with open_checkpointer(self.path) as saver:
            result, _ = await self.run_cli(build_graph(first, checkpointer=saver), request="Leave", store=store, answers=["", "/cancel"])
            self.assertEqual(result["status"], "cancelled")
            thread = next(iter(store.collection.documents.values()))["thread_id"]
        second = FakeClients()
        second.extractions = []
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(second, checkpointer=saver)
            snapshot = await load_thread(graph, thread)
            self.assertEqual(snapshot.next, ())
            self.assertFalse(any(task.interrupts for task in snapshot.tasks))
            final, output = await self.run_cli(graph, store=store, thread=thread)
        self.assertEqual(final["status"], "cancelled")
        self.assertIn("Request cancelled", output)
        self.assertEqual(second.events, [])
        self.assertEqual(len(await store.load()), 4)

    async def test_conversation_mismatch_and_thread_isolation(self):
        async with open_checkpointer(self.path) as saver:
            clients = FakeClients()
            clients.extractions = [extracted(), extracted(reason="Other reason")]
            graph = build_graph(clients, checkpointer=saver)
            for thread, conversation in [("first", "conversation-one"), ("second", "conversation-two")]:
                state = initial_state(thread, today=TODAY)
                state["conversation_id"] = conversation
                state["messages"] = [HumanMessage(content=thread, id=thread)]
                await graph.ainvoke(state, thread_config(thread), durability="sync")
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(FakeClients(), checkpointer=saver)
            first = await load_thread(graph, "first")
            second = await load_thread(graph, "second")
            self.assertEqual(first.values["leave"]["reason"], "Family vacation")
            self.assertEqual(second.values["leave"]["reason"], "Other reason")
            self.assertEqual([message.content for message in first.values["messages"]], ["first"])
            with self.assertRaisesRegex(WorkflowPersistenceError, "does not match"):
                await load_thread(graph, "first", conversation_id="conversation-two")
            with self.assertRaisesRegex(WorkflowPersistenceError, "does not match"):
                saved_conversation_id(first, "conversation-two")

    async def test_pending_submission_without_receipt_is_never_replayed(self):
        clients = FakeClients()
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(clients, checkpointer=saver)
            await graph.ainvoke(initial_state("Vacation", today=TODAY), thread_config("ambiguous"), durability="sync")
            # Model a checkpoint after confirmation and before recording a submission result.
            await graph.aupdate_state(thread_config("ambiguous"), {"confirmed": True, "status": "confirmed"}, as_node="human_review")
        second = FakeClients()
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(second, checkpointer=saver)
            with self.assertRaisesRegex(WorkflowPersistenceError, "pending POST was not repeated"):
                await self.run_cli(graph, thread="ambiguous")
        self.assertEqual(clients.submissions, [])
        self.assertEqual(second.submissions, [])

    async def test_saved_receipt_allows_finishing_success_message_only(self):
        clients = FakeClients()
        application = saved_record()
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(clients, checkpointer=saver)
            await graph.ainvoke(initial_state("Vacation", today=TODAY), thread_config("receipt"), durability="sync")
            await graph.aupdate_state(thread_config("receipt"), {"application": application, "status": "submitted"}, as_node="apply_leave")
        second = FakeClients()
        async with open_checkpointer(self.path) as saver:
            final, _ = await self.run_cli(build_graph(second, checkpointer=saver), thread="receipt")
        self.assertEqual(final["application"], application)
        self.assertEqual(second.submissions, [])
        self.assertEqual(second.success_calls, 1)

    async def test_safe_unfinished_work_continues_from_checkpoint(self):
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(FakeClients(), checkpointer=saver)
            # Save Node 1's valid output, with the two read-only API branches pending.
            state = initial_state("Vacation", today=TODAY)
            state.update({"leave": extracted().model_dump(mode="json", exclude={"stated_days", "clarification_question"}), "requested_days": 3, "status": "ready"})
            await graph.aupdate_state(thread_config("unfinished"), state, as_node="understand_request")
        second = FakeClients()
        second.extractions = []
        async with open_checkpointer(self.path) as saver:
            final, _ = await self.run_cli(build_graph(second, checkpointer=saver), thread="unfinished", answers=["no"])
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(len(second.assessments), 1)
        self.assertEqual(second.submissions, [])

    async def test_sqlite_failures_are_reported_without_reset(self):
        bad_path = Path(self.directory.name) / "missing" / "state.sqlite"
        with self.assertRaisesRegex(WorkflowPersistenceError, "not reset"):
            async with open_checkpointer(bad_path):
                self.fail("Opening a missing parent directory should fail")
        self.path.write_bytes(b"Not a SQLite database")
        with self.assertRaisesRegex(WorkflowPersistenceError, "Cannot read or write"):
            async with open_checkpointer(self.path):
                self.fail("Opening a corrupt database should fail")
        self.assertEqual(self.path.read_bytes(), b"Not a SQLite database")

    async def test_checkpoint_error_during_execution_is_sanitized(self):
        with self.assertRaisesRegex(WorkflowPersistenceError, "no workflow retry"):
            async with open_checkpointer(self.path):
                raise sqlite3.OperationalError("internal database details")

    async def test_main_recovers_conversation_from_saved_thread(self):
        store = memory()
        async with open_checkpointer(self.path) as saver:
            graph = build_graph(FakeClients(), checkpointer=saver)
            state = initial_state("Vacation", today=TODAY)
            state.update(await store.start_request("Vacation", "main-resume"))
            await graph.ainvoke(state, thread_config("main-resume"), durability="sync")
        clients = FakeClients()
        clients.extractions = []
        clients.aclose = AsyncMock()
        connect = AsyncMock(return_value=store)
        with patch.object(cli, "open_checkpointer", side_effect=lambda: open_checkpointer(self.path)):
            with patch.object(cli, "WorkflowClients", return_value=clients), patch.object(cli, "load_settings", return_value=SimpleNamespace(employee_id="EMP004", ollama_model="fake")):
                with patch.object(cli.ConversationMemory, "connect", connect), patch("sys.argv", ["main.py", "--resume-thread", "main-resume"]):
                    with patch("builtins.input", side_effect=["no", "/quit"]), redirect_stdout(io.StringIO()):
                        self.assertEqual(await cli.main(), 0)
        connect.assert_awaited_once_with(store.conversation_id)
        clients.aclose.assert_awaited_once()
        store.client.close.assert_awaited_once()
        self.assertEqual(clients.submissions, [])


if __name__ == "__main__":
    unittest.main()
