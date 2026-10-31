"""Run from LangGraph: python -m unittest discover -s langGraph_PoC/tests -v."""

import asyncio
import copy
from datetime import date, datetime, timezone
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
from contextlib import redirect_stdout

import httpx
from langgraph.types import Command

from langGraph_PoC.clients import SubmissionUnknownError, WorkflowClients, WorkflowError
from langGraph_PoC.config import Settings, load_settings
from langGraph_PoC.models import EligibilityVerdict, ExtractedRequest
from langGraph_PoC.workflow import (
    LeaveWorkflow, build_graph, count_weekdays, end_date_for_days,
    initial_state, normalize_request,
)


TODAY = date(2026, 10, 31)
EMPLOYEE = {
    "employee_id": "EMP004", "name": "Arjun Rao", "email": "arjun.rao@example.com",
    "department": "Engineering", "designation": "Software Engineer",
    "joining_date": "2022-03-14", "manager_id": "EMP002", "leave_balance": 12,
}
LEAVE = {
    "start_date": "2026-11-10", "end_date": "2026-11-12",
    "leave_type": "annual", "reason": "Family vacation",
}
CHUNKS = [{"chunk_id": "HR001", "title": "Leave Policy", "content": "Planned leave requires three working days notice."}]


def extracted(**updates):
    return ExtractedRequest(**{**LEAVE, **updates})


def verdict(**updates):
    return EligibilityVerdict(**{
        "eligible": True, "policy_supported": True,
        "reasons": ["The leave policy permits this request with adequate notice."],
        "supporting_chunk_ids": ["HR001"], **updates,
    })


def config():
    return {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 100}


def saved_record(employee=EMPLOYEE, leave=LEAVE):
    return {
        "application_id": str(uuid4()), "submitted_at": datetime.now(timezone.utc).isoformat(),
        "employee": copy.deepcopy(employee), "leave": copy.deepcopy(leave),
    }


class FakeClients:
    def __init__(self):
        self.extractions = [extracted()]
        self.extraction_contexts = []
        self.employee = copy.deepcopy(EMPLOYEE)
        self.policy_verdict = verdict()
        self.assessments = []
        self.events = []
        self.employee_started = asyncio.Event()
        self.policy_started = asyncio.Event()
        self.submissions = []
        self.employee_error = self.policy_error = self.assessment_error = None
        self.submit_error = self.success_error = self.explain_error = None
        self.success_calls = self.explain_calls = 0

    async def extract_request(self, context):
        self.extraction_contexts.append(copy.deepcopy(context))
        item = self.extractions.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get_employee(self):
        self.events.append("employee_started")
        self.employee_started.set()
        await asyncio.wait_for(self.policy_started.wait(), timeout=2)
        self.events.append("employee_completed")
        if self.employee_error:
            raise self.employee_error
        return copy.deepcopy(self.employee)

    async def get_policies(self, query):
        self.events.append("policy_started")
        self.policy_started.set()
        await asyncio.wait_for(self.employee_started.wait(), timeout=2)
        self.events.append("policy_completed")
        if self.policy_error:
            raise self.policy_error
        return copy.deepcopy(CHUNKS)

    async def assess_eligibility(self, context):
        self.assessments.append(copy.deepcopy(context))
        self.events.append("eligibility")
        if self.assessment_error:
            raise self.assessment_error
        return self.policy_verdict

    async def explain_rejection(self, context):
        self.explain_calls += 1
        if self.explain_error:
            raise self.explain_error
        return "Ineligible: " + " ".join(context["eligibility"]["reasons"])

    async def submit_leave(self, employee, leave):
        self.submissions.append((copy.deepcopy(employee), copy.deepcopy(leave)))
        if self.submit_error:
            raise self.submit_error
        return saved_record(employee, leave)

    async def success_message(self, application):
        self.success_calls += 1
        if self.success_error:
            raise self.success_error
        return (
            f"Application {application['application_id']} submitted for "
            f"{application['leave']['start_date']} to {application['leave']['end_date']}."
        )


class DateAndConfigTests(unittest.TestCase):
    def test_weekday_count_and_derived_end(self):
        self.assertEqual(count_weekdays(date(2026, 11, 6), date(2026, 11, 9)), 2)
        self.assertEqual(count_weekdays(date(2026, 11, 7), date(2026, 11, 8)), 0)
        self.assertEqual(count_weekdays(date(2026, 11, 9), date(2026, 11, 9)), 1)
        self.assertEqual(count_weekdays(date(2026, 11, 10), date(2026, 11, 9)), 0)
        self.assertEqual(end_date_for_days(date(2026, 11, 6), 3), date(2026, 11, 10))
        self.assertEqual(end_date_for_days(date(2026, 11, 7), 1), date(2026, 11, 9))
        with self.assertRaises(ValueError):
            end_date_for_days(TODAY, 0)

    def test_default_type_and_missing_details(self):
        leave, question = normalize_request(extracted(leave_type=None))
        self.assertIsNone(question)
        self.assertEqual(leave.leave_type, "annual")
        leave, question = normalize_request(ExtractedRequest())
        self.assertIsNone(leave)
        self.assertIn("start date", question)
        self.assertIn("reason", question)

    def test_derive_end_and_conflicting_count(self):
        leave, question = normalize_request(extracted(start_date="2026-11-06", end_date=None, stated_days=3))
        self.assertIsNone(question)
        self.assertEqual(leave.end_date, date(2026, 11, 10))
        leave, question = normalize_request(extracted(stated_days=5))
        self.assertIsNone(leave)
        self.assertIn("contain 3 working days", question)
        self.assertIn("requested 5", question)

    def test_reversed_dates_and_blank_reason_need_clarification(self):
        leave, question = normalize_request(extracted(end_date="2026-11-09", reason="   "))
        self.assertIsNone(leave)
        self.assertIn("before the start", question)
        self.assertIn("reason", question)

    def test_key_file_precedes_environment_and_is_not_in_repr(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("OLLAMA_API_KEY=file-test-key\n", encoding="utf-8")
            with patch.dict(os.environ, {"OLLAMA_API_KEY": "inherited-test-key"}):
                settings = load_settings(path)
            self.assertEqual(settings.ollama_api_key, "file-test-key")
            self.assertNotIn("file-test-key", repr(settings))
            self.assertEqual(settings.ollama_model, "gpt-oss:20b")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Missing OLLAMA_API_KEY"):
                load_settings(Path(folder) / "missing.env")


class GraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_join_confirmation_then_one_submission(self):
        clients = FakeClients()
        graph = build_graph(clients)
        thread = config()
        paused = await graph.ainvoke(initial_state("Three days for vacation", today=TODAY), thread)
        self.assertEqual(paused["__interrupt__"][0].value["kind"], "confirmation")
        self.assertEqual(clients.submissions, [])
        self.assertEqual(set(clients.events[:2]), {"employee_started", "policy_started"})
        self.assertGreater(clients.events.index("eligibility"), clients.events.index("employee_completed"))
        self.assertGreater(clients.events.index("eligibility"), clients.events.index("policy_completed"))
        self.assertEqual(len(clients.assessments), 1)
        self.assertEqual(clients.assessments[0]["requested_days"], 3)
        self.assertEqual(clients.assessments[0]["working_days_notice"], 7)
        final = await graph.ainvoke(Command(resume=True), thread)
        self.assertEqual(final["status"], "submitted")
        self.assertEqual(len(clients.submissions), 1)
        self.assertIn(final["application"]["application_id"], final["final_message"])
        await graph.ainvoke(Command(resume=True), thread)
        self.assertEqual(len(clients.submissions), 1)

    async def test_decline_and_nonboolean_confirmation_never_submit(self):
        clients = FakeClients()
        graph = build_graph(clients)
        thread = config()
        await graph.ainvoke(initial_state("Vacation", today=TODAY), thread)
        still_paused = await graph.ainvoke(Command(resume="yes"), thread)
        self.assertIn("__interrupt__", still_paused)
        self.assertEqual(clients.submissions, [])
        final = await graph.ainvoke(Command(resume=False), thread)
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(clients.submissions, [])

    async def test_clarification_persists_without_replaying_extraction_before_interrupt(self):
        clients = FakeClients()
        clients.extractions = [extracted(reason=None), extracted()]
        graph = build_graph(clients)
        thread = config()
        paused = await graph.ainvoke(initial_state("Leave November 10-12", today=TODAY), thread)
        self.assertEqual(paused["__interrupt__"][0].value["kind"], "clarification")
        self.assertEqual(len(clients.extraction_contexts), 1)
        self.assertEqual(clients.events, [])
        paused = await graph.ainvoke(Command(resume="Family vacation"), thread)
        self.assertEqual(paused["__interrupt__"][0].value["kind"], "confirmation")
        self.assertEqual(len(clients.extraction_contexts), 2)
        self.assertEqual(clients.extraction_contexts[1]["clarification_history"][0]["answer"], "Family vacation")
        self.assertEqual(paused["clarification_history"][0]["answer"], "Family vacation")
        self.assertEqual(clients.submissions, [])
        final = await graph.ainvoke(Command(resume=True), thread)
        self.assertEqual(final["status"], "submitted")
        self.assertEqual(len(clients.extraction_contexts), 2)

    async def test_conflicting_counts_trigger_followup_and_date_anchors_reach_model(self):
        clients = FakeClients()
        clients.extractions = [extracted(stated_days=5), extracted(stated_days=3)]
        graph = build_graph(clients)
        thread = config()
        paused = await graph.ainvoke(initial_state("Five days November 10-12", today=TODAY), thread)
        self.assertIn("contain 3 working days", paused["__interrupt__"][0].value["question"])
        context = clients.extraction_contexts[0]
        self.assertEqual(context["current_date"], "2026-10-31")
        self.assertEqual(context["next_monday"], "2026-11-02")
        self.assertEqual(context["timezone"], "Asia/Kolkata")
        paused = await graph.ainvoke(Command(resume="Use three working days",), thread)
        self.assertEqual(paused["requested_days"], 3)
        self.assertEqual(paused["__interrupt__"][0].value["kind"], "confirmation")

    async def test_policy_rejection_and_explanation_fallback(self):
        clients = FakeClients()
        clients.policy_verdict = verdict(eligible=False, reasons=["Insufficient notice under HR001."])
        clients.explain_error = WorkflowError("Ollama unavailable")
        final = await build_graph(clients).ainvoke(initial_state("Vacation", today=TODAY), config())
        self.assertEqual(final["status"], "ineligible")
        self.assertIn("Insufficient notice", final["final_message"])
        self.assertNotIn("__interrupt__", final)
        self.assertEqual(clients.submissions, [])

    async def test_python_guards_override_positive_model_verdict(self):
        for draft, balance, expected in [
            (extracted(), 1, "only 1 days are available"),
            (extracted(start_date="2026-10-29", end_date="2026-10-30"), 12, "in the past"),
            (extracted(start_date="2026-11-07", end_date="2026-11-08"), 12, "no working days"),
        ]:
            with self.subTest(expected=expected):
                clients = FakeClients()
                clients.extractions = [draft]
                clients.employee["leave_balance"] = balance
                final = await build_graph(clients).ainvoke(initial_state("Leave", today=TODAY), config())
                self.assertEqual(final["status"], "ineligible")
                self.assertTrue(any(expected in reason for reason in final["eligibility"]["reasons"]))
                self.assertEqual(clients.submissions, [])

    async def test_missing_or_fabricated_evidence_is_not_eligible(self):
        for policy_verdict in [
            verdict(policy_supported=False), verdict(supporting_chunk_ids=[]),
            verdict(supporting_chunk_ids=["INVENTED"]),
        ]:
            clients = FakeClients()
            clients.policy_verdict = policy_verdict
            final = await build_graph(clients).ainvoke(initial_state("Leave", today=TODAY), config())
            self.assertFalse(final["eligibility"]["eligible"])
            self.assertEqual(clients.submissions, [])

    async def test_lookup_and_model_errors_stop_without_submission(self):
        for source in ("employee", "policy", "assessment", "extraction"):
            with self.subTest(source=source):
                clients = FakeClients()
                if source == "extraction":
                    clients.extractions = [WorkflowError("Invalid structured data")]
                else:
                    setattr(clients, source + "_error", WorkflowError(source + " failed"))
                final = await build_graph(clients).ainvoke(initial_state("Leave", today=TODAY), config())
                self.assertEqual(final["status"], "error")
                self.assertEqual(clients.submissions, [])
                if source in ("employee", "policy", "extraction"):
                    self.assertEqual(clients.assessments, [])

    async def test_unknown_submission_and_success_fallback(self):
        for unknown in (True, False):
            clients = FakeClients()
            if unknown:
                clients.submit_error = SubmissionUnknownError("Submission outcome unknown; no retry was made.")
            else:
                clients.success_error = WorkflowError("Message generation failed")
            graph = build_graph(clients)
            thread = config()
            await graph.ainvoke(initial_state("Leave", today=TODAY), thread)
            final = await graph.ainvoke(Command(resume=True), thread)
            self.assertEqual(len(clients.submissions), 1)
            if unknown:
                self.assertEqual(final["status"], "submission_unknown")
                self.assertEqual(clients.success_calls, 0)
            else:
                self.assertEqual(final["status"], "submitted")
                self.assertIn(final["application"]["application_id"], final["final_message"])

    async def test_threads_keep_requests_and_confirmation_separate(self):
        clients = FakeClients()
        clients.extractions = [extracted(), extracted(start_date="2026-11-16", end_date="2026-11-17", reason="Personal work")]
        graph = build_graph(clients)
        first, second = config(), config()
        await graph.ainvoke(initial_state("Vacation", today=TODAY), first)
        await graph.ainvoke(initial_state("Personal work", today=TODAY), second)
        cancelled = await graph.ainvoke(Command(resume=False), first)
        submitted = await graph.ainvoke(Command(resume=True), second)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(submitted["leave"]["reason"], "Personal work")
        self.assertEqual(len(clients.submissions), 1)
        self.assertEqual(clients.submissions[0][1]["start_date"], "2026-11-16")

    async def test_submission_node_guard(self):
        clients = FakeClients()
        node = LeaveWorkflow(clients)
        for state in ({}, {"confirmed": "yes", "eligibility": {"eligible": True}}, {"confirmed": True, "eligibility": {"eligible": False}}):
            result = await node.apply_leave(state)
            self.assertEqual(result["status"], "error")
        self.assertEqual(clients.submissions, [])

    async def test_terminal_confirmation_uses_interrupt_and_resume(self):
        from langGraph_PoC import main as cli

        clients = FakeClients()
        graph = build_graph(clients)
        with patch.object(cli, "initial_state", side_effect=lambda text: initial_state(text, today=TODAY)):
            with patch("builtins.input", side_effect=["maybe", "yes"]), redirect_stdout(io.StringIO()) as output:
                final = await cli.run_request(graph, "Vacation")
        self.assertEqual(final["status"], "submitted")
        self.assertIn("Please enter yes or no", output.getvalue())
        self.assertEqual(len(clients.submissions), 1)


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(copy.deepcopy(messages))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_repair_is_limited_to_one_and_uses_prompt_schema(self):
        model = FakeModel(["not json", extracted().model_dump_json()])
        clients = WorkflowClients(Settings(ollama_api_key="secret-test"), model=model)
        try:
            result = await clients.extract_request({"current_date": "2026-10-31"})
            self.assertEqual(result.reason, "Family vacation")
            self.assertEqual(len(model.calls), 2)
            self.assertIn("schema", model.calls[0][0][1])
            self.assertNotIn("secret-test", json.dumps(model.calls))
            clients.model = FakeModel(["bad", "still bad"])
            with self.assertRaisesRegex(WorkflowError, "one repair attempt"):
                await clients.extract_request({})
            self.assertEqual(len(clients.model.calls), 2)
        finally:
            await clients.aclose()

    async def test_empty_model_output_and_transport_errors_are_sanitized(self):
        for response in ("", RuntimeError("secret-test sensitive transport detail")):
            clients = WorkflowClients(Settings(ollama_api_key="secret-test"), model=FakeModel([response]))
            try:
                with self.assertRaises(WorkflowError) as error:
                    await clients.success_message({})
                self.assertNotIn("secret-test", str(error.exception))
            finally:
                await clients.aclose()

    async def test_api_contracts_and_exact_submission_payload(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=EMPLOYEE)
            body = json.loads(request.content)
            if request.url.path == "/policies/search":
                return httpx.Response(200, json={"query": body["query"], "chunks": CHUNKS})
            self.assertEqual(body, {"employee": EMPLOYEE, "leave": LEAVE})
            return httpx.Response(201, json=saved_record())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            clients = WorkflowClients(Settings(ollama_api_key="unused"), model=FakeModel([]), http_client=http)
            self.assertEqual(await clients.get_employee(), EMPLOYEE)
            self.assertEqual(await clients.get_policies("Leave question"), CHUNKS)
            record = await clients.submit_leave(EMPLOYEE, LEAVE)
            self.assertEqual(record["leave"], LEAVE)
        self.assertEqual([request.url.path for request in requests], ["/employees/EMP004", "/policies/search", "/leave-applications"])
        self.assertTrue(all("authorization" not in request.headers for request in requests))

    async def test_submission_failures_are_not_retried(self):
        for outcome in ("timeout", "500", "422", "malformed", "mismatched"):
            calls = []

            def handler(request):
                calls.append(request)
                if outcome == "timeout":
                    raise httpx.ReadTimeout("timeout", request=request)
                if outcome in ("500", "422"):
                    return httpx.Response(int(outcome), json={"detail": "failure"})
                if outcome == "malformed":
                    return httpx.Response(201, json={"unexpected": True})
                record = saved_record()
                record["leave"]["reason"] = "Different request"
                return httpx.Response(201, json=record)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                clients = WorkflowClients(Settings(ollama_api_key="unused"), model=FakeModel([]), http_client=http)
                expected_error = WorkflowError if outcome == "422" else SubmissionUnknownError
                with self.assertRaises(expected_error):
                    await clients.submit_leave(EMPLOYEE, LEAVE)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
