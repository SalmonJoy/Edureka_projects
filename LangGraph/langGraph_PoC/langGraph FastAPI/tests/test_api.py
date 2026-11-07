"""Real FastAPI routes and temporary SQLite; no live service calls."""

import asyncio
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

import httpx

import app as wrapper
import api_service
from langGraph_PoC.clients import SubmissionUnknownError, WorkflowError
from langGraph_PoC.conversation_memory import ConversationMemory, ConversationStorageError
from langGraph_PoC.workflow import initial_state
from langGraph_PoC.workflow_persistence import thread_config
from langGraph_PoC.tests.test_conversation_memory import FakeCollection
from langGraph_PoC.tests.test_workflow import FakeClients, TODAY, extracted, saved_record, verdict


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_tracing_one_parent_across_clarification_restart_and_confirmation(self):
        from unittest.mock import MagicMock
        from langsmith import Client
        from langGraph_PoC import tracing

        sdk = MagicMock(spec=Client)
        self.clients.extractions = [extracted(reason=None), extracted()]
        with patch.object(tracing, "_client", sdk), patch.object(tracing, "_project", "test-project"):
            state = await self.start("Leave November 10-12")
            tracing.flush_traces()
            before = sdk.create_run.call_count, sdk.update_run.call_count
            self.assertEqual((await self.http.get(f"/workflows/{state['thread_id']}")).status_code, 200)
            self.assertEqual(before, (sdk.create_run.call_count, sdk.update_run.call_count))
            self.assertEqual(self.clients.submissions, [])
            await self.close_app()
            await self.open_app()
            confirm = (await self.answer(state, "Family vacation")).json()
            self.assertEqual(confirm["interrupt"]["kind"], "confirmation")
            self.assertEqual(self.clients.submissions, [])
            final = await self.answer(confirm, True)
            self.assertEqual(final.json()["status"], "submitted")
            self.assertEqual(len(self.clients.submissions), 1)
            tracing.flush_traces()
            calls = sdk.create_run.call_args_list
            parents = [c for c in calls if c.kwargs["name"] == "Leave request"]
            self.assertEqual(len(parents), 1)
            root_id = str(parents[0].kwargs["id"])
            self.assertTrue(all(str(c.kwargs["trace_id"]) == root_id for c in calls))
            self.assertTrue(all(c.kwargs.get("parent_run_id") for c in calls
                                if c.kwargs["name"] != "Leave request"))
            before = sdk.create_run.call_count, sdk.update_run.call_count
            recovered = await self.http.post(f"/workflows/{state['thread_id']}/resume", json={})
            self.assertEqual(recovered.json()["application"], final.json()["application"])
            tracing.flush_traces()
            self.assertEqual(before, (sdk.create_run.call_count, sdk.update_run.call_count))
            self.assertEqual(len(self.clients.submissions), 1)

    async def asyncSetUp(self):
        self.temp = TemporaryDirectory(prefix="langgraph-fastapi-test-")
        self.db_path = Path(self.temp.name) / "workflow_state.sqlite"
        self.clients = FakeClients()
        self.clients.aclose = AsyncMock()
        self.collection = FakeCollection()
        self.connections = []

        async def memory_factory(conversation_id):
            store = ConversationMemory(
                SimpleNamespace(close=AsyncMock()), self.collection,
                conversation_id if conversation_id is not None else "startup-probe",
            )
            await store.load()
            self.connections.append(store)
            return store

        self.memory_factory = memory_factory
        self.clock = patch.object(api_service, "initial_state", side_effect=lambda text: initial_state(text, today=TODAY))
        self.clock.start()
        await self.open_app()

    async def open_app(self):
        self.application = wrapper.create_app(
            clients_factory=lambda settings: self.clients, memory_factory=self.memory_factory,
            settings_factory=lambda: SimpleNamespace(), checkpoint_path=self.db_path,
        )
        self.lifespan = self.application.router.lifespan_context(self.application)
        await self.lifespan.__aenter__()
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url="http://test")

    async def close_app(self):
        await self.http.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def asyncTearDown(self):
        await self.close_app()
        self.clock.stop()
        self.temp.cleanup()

    async def start(self, request="Vacation", conversation_id=None):
        body = {"request": request}
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
        response = await self.http.post("/workflows", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def answer(self, state, answer, *, prompt_id=None):
        return await self.http.post(
            f"/workflows/{state['thread_id']}/resume",
            json={"prompt_id": prompt_id or state["interrupt"]["prompt_id"], "answer": answer},
        )

    async def test_start_get_confirmation_and_single_submission(self):
        state = await self.start()
        self.assertEqual(state["status"], "awaiting_confirmation")
        self.assertEqual(state["interrupt"]["prompt_id"], "confirmation")
        self.assertEqual(state["interrupt"]["details"]["requested_days"], 3)
        self.assertIsNone(state["application"])
        self.assertEqual(self.clients.submissions, [])
        before = len(self.connections), len(self.collection.documents), list(self.clients.events)
        inspected = await self.http.get(f"/workflows/{state['thread_id']}")
        self.assertEqual(inspected.status_code, 200)
        self.assertEqual(inspected.json(), state)
        self.assertEqual(before, (len(self.connections), len(self.collection.documents), self.clients.events))
        response = await self.answer(state, True)
        self.assertEqual(response.status_code, 200, response.text)
        final = response.json()
        self.assertEqual(final["status"], "submitted")
        self.assertIsNone(final["interrupt"])
        self.assertIn(final["application"]["application_id"], final["message"])
        self.assertEqual(len(self.clients.submissions), 1)
        self.assertEqual(len(self.collection.documents), 4)
        duplicate = await self.answer(state, True)
        self.assertEqual(duplicate.status_code, 409)
        recovered = await self.http.post(f"/workflows/{state['thread_id']}/resume", json={})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["application"], final["application"])
        self.assertEqual(len(self.clients.submissions), 1)
        self.assertEqual(len(self.collection.documents), 4)

    async def test_clarification_stale_id_and_decline(self):
        self.clients.extractions = [extracted(reason=None), extracted()]
        state = await self.start("Leave November 10-12")
        self.assertEqual(state["interrupt"]["kind"], "clarification")
        self.assertEqual(state["interrupt"]["prompt_id"], "clarification:0")
        wrong_type = await self.answer(state, True)
        self.assertEqual(wrong_type.status_code, 422)
        clarified = await self.answer(state, "Family vacation")
        self.assertEqual(clarified.status_code, 200, clarified.text)
        confirm = clarified.json()
        self.assertEqual(confirm["interrupt"]["kind"], "confirmation")
        stale = await self.answer(state, "Family vacation")
        self.assertEqual(stale.status_code, 409)
        declined = await self.answer(confirm, False)
        self.assertEqual(declined.status_code, 200)
        self.assertEqual(declined.json()["status"], "cancelled")
        self.assertEqual(self.clients.submissions, [])
        self.assertEqual(len(self.collection.documents), 6)

    async def test_multiple_clarifications_have_new_prompt_ids(self):
        self.clients.extractions = [extracted(reason=None), extracted(stated_days=5), extracted(stated_days=3)]
        first = await self.start("Leave")
        second = (await self.answer(first, "Family vacation")).json()
        self.assertEqual(second["interrupt"]["prompt_id"], "clarification:1")
        self.assertEqual((await self.answer(first, "Vacation")).status_code, 409)
        third = await self.answer(second, "Three working days")
        self.assertEqual(third.status_code, 200)
        self.assertEqual(third.json()["interrupt"]["kind"], "confirmation")

    async def test_invalid_requests_and_strict_boolean(self):
        for body in [{}, {"request": ""}, {"request": " \n"}, {"request": 42}, {"request": "Leave", "conversation_id": " "}]:
            self.assertEqual((await self.http.post("/workflows", json=body)).status_code, 422)
        state = await self.start()
        for body in [
            {"prompt_id": "confirmation", "answer": "yes"},
            {"prompt_id": "confirmation", "answer": 1},
            {"prompt_id": "confirmation"}, {"answer": True},
            {"prompt_id": None, "answer": None},
            {"prompt_id": "confirmation", "answer": "  "},
        ]:
            response = await self.http.post(f"/workflows/{state['thread_id']}/resume", json=body)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.clients.submissions, [])

    async def test_not_found_and_api_checkpoint_location(self):
        self.assertEqual((await self.http.get("/workflows/missing")).status_code, 404)
        self.assertEqual((await self.http.post("/workflows/missing/resume", json={})).status_code, 404)
        self.assertEqual((await self.http.post("/workflows/missing/cancel")).status_code, 404)
        self.assertTrue(self.db_path.is_file())
        self.assertEqual(wrapper.API_DB_PATH.parent, API_ROOT)
        self.assertNotEqual(wrapper.API_DB_PATH, API_ROOT.parent / "workflow_state.sqlite")

    async def test_eligibility_rejection_and_model_errors_are_workflow_results(self):
        self.clients.policy_verdict = verdict(eligible=False, reasons=["Insufficient policy notice."])
        state = await self.start()
        self.assertEqual(state["status"], "ineligible")
        self.assertIn("Insufficient", state["message"])
        self.assertIsNone(state["interrupt"])
        self.clients.extractions = [WorkflowError("Model unavailable")]
        error = await self.start("Another request")
        self.assertEqual(error["status"], "error")
        self.assertEqual(error["message"], "Model unavailable")
        self.assertEqual(self.clients.submissions, [])

    async def test_cancel_pending_and_idempotent_cancellation(self):
        self.clients.extractions = [extracted(reason=None)]
        state = await self.start()
        path = f"/workflows/{state['thread_id']}/cancel"
        cancelled = await self.http.post(path)
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertIsNone(cancelled.json()["interrupt"])
        count = len(self.collection.documents)
        self.assertEqual((await self.http.post(path)).status_code, 200)
        self.assertEqual(len(self.collection.documents), count)
        self.assertEqual((await self.answer(state, "Vacation")).status_code, 409)
        self.assertEqual(self.clients.submissions, [])

    async def test_cannot_cancel_submitted_workflow(self):
        state = await self.start()
        self.assertEqual((await self.answer(state, True)).status_code, 200)
        self.assertEqual((await self.http.post(f"/workflows/{state['thread_id']}/cancel")).status_code, 409)
        self.assertEqual(len(self.clients.submissions), 1)

    async def test_sqlite_restart_restores_pending_prompt_without_message_duplication(self):
        state = await self.start()
        self.assertEqual(len(self.collection.documents), 2)
        await self.close_app()
        self.clients.extractions = []
        before = list(self.clients.events)
        await self.open_app()
        inspected = await self.http.get(f"/workflows/{state['thread_id']}")
        self.assertEqual(inspected.json()["interrupt"]["prompt_id"], "confirmation")
        recovery = await self.http.post(f"/workflows/{state['thread_id']}/resume", json={})
        self.assertEqual(recovery.status_code, 200)
        self.assertEqual(len(self.collection.documents), 2)
        final = await self.answer(recovery.json(), True)
        self.assertEqual(final.status_code, 200, final.text)
        self.assertEqual(final.json()["status"], "submitted")
        self.assertEqual(self.clients.events, before)
        self.assertEqual(len(self.collection.documents), 4)

    async def test_restart_at_clarification_uses_saved_question_and_new_answer(self):
        self.clients.extractions = [extracted(reason=None), extracted()]
        state = await self.start("Leave November 10-12")
        await self.close_app()
        await self.open_app()
        recovery = await self.http.post(f"/workflows/{state['thread_id']}/resume", json={})
        self.assertEqual(recovery.status_code, 200)
        self.assertEqual(recovery.json()["interrupt"]["prompt_id"], "clarification:0")
        self.assertEqual(len(self.collection.documents), 2)
        clarified = await self.answer(recovery.json(), "Family vacation")
        self.assertEqual(clarified.status_code, 200, clarified.text)
        self.assertEqual(clarified.json()["interrupt"]["kind"], "confirmation")
        self.assertEqual(len(self.clients.extraction_contexts), 2)
        self.assertEqual(len(self.collection.documents), 4)
        self.assertEqual(self.clients.submissions, [])

    async def test_empty_resume_continues_safe_unfinished_nodes(self):
        service = self.application.state.workflow_service
        store = await self.memory_factory("unfinished-conversation")
        state = initial_state("Prepared request", today=TODAY)
        state.update(await store.start_request("Prepared request", "unfinished-thread"))
        state.update({
            "leave": extracted().model_dump(mode="json", exclude={"stated_days", "clarification_question"}),
            "requested_days": 3, "status": "ready",
        })
        await service.graph.aupdate_state(thread_config("unfinished-thread"), state, as_node="understand_request")
        await store.close()
        self.clients.extractions = []
        result = await self.http.post("/workflows/unfinished-thread/resume", json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["interrupt"]["kind"], "confirmation")
        self.assertEqual(len(self.clients.assessments), 1)
        self.assertEqual(self.clients.submissions, [])

    async def test_same_conversation_loads_history_with_fresh_confirmation(self):
        self.clients.extractions = [extracted(), extracted()]
        first = await self.start(conversation_id="shared-session")
        receipt = (await self.answer(first, True)).json()
        second = await self.start("Use the same dates and reason", "shared-session")
        self.assertNotEqual(first["thread_id"], second["thread_id"])
        self.assertEqual(second["conversation_id"], first["conversation_id"])
        self.assertEqual(second["status"], "awaiting_confirmation")
        self.assertIn(receipt["application"]["application_id"], str(self.clients.extraction_contexts[1]["conversation_history"]))
        self.assertEqual(len(self.clients.submissions), 1)
        self.assertEqual((await self.answer(second, False)).status_code, 200)
        self.assertEqual(len(self.clients.submissions), 1)

    async def test_conversation_lock_rejects_overlap_but_allows_other_conversation(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def extraction(context):
            if context["original_request"] == "slow":
                started.set()
                await release.wait()
            return extracted()

        self.clients.extract_request = extraction
        task = asyncio.create_task(self.http.post("/workflows", json={"request": "slow", "conversation_id": "same"}))
        try:
            await asyncio.wait_for(started.wait(), 3)
            overlap = await self.http.post("/workflows", json={"request": "second", "conversation_id": "same"})
            self.assertEqual(overlap.status_code, 409)
            different = await asyncio.wait_for(self.http.post("/workflows", json={"request": "independent", "conversation_id": "other"}), 5)
            self.assertEqual(different.status_code, 201, different.text)
        finally:
            release.set()
            first = await task
        self.assertEqual(first.status_code, 201)

    async def test_storage_failures_and_saved_receipt_warning(self):
        self.collection.fail_message = lambda message: True
        failed = await self.http.post("/workflows", json={"request": "Vacation"})
        self.assertEqual(failed.status_code, 503)
        self.assertIn("thread_id", failed.json())
        self.collection.fail_message = lambda message: False
        state = await self.start()
        self.collection.fail_message = lambda message: message["type"] == "human" and message["data"]["content"] == "yes"
        no_confirmation = await self.answer(state, True)
        self.assertEqual(no_confirmation.status_code, 503)
        self.assertEqual(self.clients.submissions, [])
        self.collection.fail_message = lambda message: "submitted for" in message["data"]["content"]
        receipt = await self.answer(state, True)
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.assertEqual(receipt.json()["status"], "submitted")
        self.assertIsNotNone(receipt.json()["application"])
        self.assertTrue(receipt.json()["warnings"])
        self.assertEqual(len(self.clients.submissions), 1)

    async def test_concurrent_approvals_do_not_submit_twice(self):
        state = await self.start()
        started, release = asyncio.Event(), asyncio.Event()
        original_submit = self.clients.submit_leave

        async def slow_submit(employee, leave):
            started.set()
            await release.wait()
            return await original_submit(employee, leave)

        self.clients.submit_leave = slow_submit
        task = asyncio.create_task(self.answer(state, True))
        try:
            await asyncio.wait_for(started.wait(), 3)
            duplicate = await self.answer(state, True)
            self.assertEqual(duplicate.status_code, 409)
        finally:
            release.set()
            result = await task
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.clients.submissions), 1)

    async def test_sqlite_execution_failure_returns_503_without_submission(self):
        service = self.application.state.workflow_service
        with patch.object(service.graph, "ainvoke", side_effect=sqlite3.OperationalError("private database failure")):
            response = await self.http.post("/workflows", json={"request": "Vacation"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.assertIn("thread_id", response.json())
        self.assertEqual(self.clients.submissions, [])

    async def test_sqlite_get_failure_is_sanitized(self):
        service = self.application.state.workflow_service
        with patch.object(service.graph, "aget_state", side_effect=sqlite3.OperationalError("sensitive internal details")):
            response = await self.http.get("/workflows/example")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sensitive", response.text)

    async def test_pending_post_without_receipt_is_blocked_and_known_receipt_can_finish(self):
        state = await self.start()
        service = self.application.state.workflow_service
        config = thread_config(state["thread_id"])
        await service.graph.aupdate_state(config, {"confirmed": True, "status": "confirmed"}, as_node="human_review")
        blocked = await self.http.post(f"/workflows/{state['thread_id']}/resume", json={})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.clients.submissions, [])
        application = saved_record()
        await service.graph.aupdate_state(config, {"application": application, "status": "submitted"}, as_node="apply_leave")
        recovered = await self.http.post(f"/workflows/{state['thread_id']}/resume", json={})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["application"]["application_id"], application["application_id"])
        self.assertEqual(self.clients.submissions, [])

    async def test_unknown_submission_result_is_not_retried(self):
        state = await self.start()
        self.clients.submit_error = SubmissionUnknownError("Outcome unknown; no retry was made.")
        final = await self.answer(state, True)
        self.assertEqual(final.status_code, 200)
        self.assertEqual(final.json()["status"], "submission_unknown")
        recovered = await self.http.post(f"/workflows/{state['thread_id']}/resume", json={})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(len(self.clients.submissions), 1)

    async def test_docs_and_openapi(self):
        self.assertEqual((await self.http.get("/docs")).status_code, 200)
        schema = (await self.http.get("/openapi.json")).json()
        self.assertEqual(set(schema["paths"]), {"/workflows", "/workflows/{thread_id}", "/workflows/{thread_id}/resume", "/workflows/{thread_id}/cancel"})
        models = schema["components"]["schemas"]
        self.assertEqual(set(models["WorkflowResponse"]["properties"]), {"thread_id", "conversation_id", "status", "message", "interrupt", "application", "warnings"})
        self.assertIn("201", schema["paths"]["/workflows"]["post"]["responses"])
        self.assertIn("503", schema["paths"]["/workflows/{thread_id}"]["get"]["responses"])


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_mongo_startup_closes_clients_and_leaves_database_reopenable(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            clients = SimpleNamespace(aclose=AsyncMock())
            application = wrapper.create_app(
                clients_factory=lambda settings: clients,
                settings_factory=lambda: SimpleNamespace(), checkpoint_path=path,
                memory_factory=AsyncMock(side_effect=ConversationStorageError("private connection details")),
            )
            with self.assertRaisesRegex(RuntimeError, "initialization/storage failed") as caught:
                async with application.router.lifespan_context(application):
                    self.fail("Startup should fail")
            self.assertNotIn("private", str(caught.exception))
            clients.aclose.assert_awaited_once()
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
