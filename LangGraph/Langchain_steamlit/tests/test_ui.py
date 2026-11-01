"""HTTP contract and Streamlit interaction tests; all backend calls are fake."""

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

UI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_ROOT))

import httpx
from streamlit.testing.v1 import AppTest

from api_client import ApiClientError, WorkflowApiClient, normalize_url, validate_workflow
import ui_state


EMPLOYEE = {
    "employee_id": "EMP004", "name": "Arjun Rao", "email": "arjun.rao@example.com",
    "department": "Engineering", "designation": "Software Engineer",
    "joining_date": "2022-03-14", "manager_id": "EMP002", "leave_balance": 12,
}
LEAVE = {"start_date": "2026-11-16", "end_date": "2026-11-18", "leave_type": "annual", "reason": "Family vacation"}
RECEIPT = {
    "application_id": "11111111-1111-4111-8111-111111111111", "submitted_at": "2026-11-01T10:00:00Z",
    "employee": EMPLOYEE, "leave": LEAVE,
}


def workflow(status="awaiting_confirmation", *, thread="thread-1", conversation="conversation-1", warnings=None):
    pending = None
    if status == "awaiting_confirmation":
        pending = {
            "prompt_id": "confirmation", "kind": "confirmation", "question": "Submit this application?",
            "details": {"employee": copy.deepcopy(EMPLOYEE), "leave": copy.deepcopy(LEAVE),
                        "requested_days": 3, "leave_balance": 12,
                        "eligibility": {"eligible": True, "reasons": ["Adequate policy notice and leave balance."], "supporting_chunk_ids": ["HR001"]}},
        }
    elif status == "clarifying":
        pending = {"prompt_id": "clarification:0", "kind": "clarification", "question": "What is your leave reason?", "details": {"known_details": {}}}
    messages = {
        "awaiting_confirmation": "Review your leave before submitting.", "clarifying": "What is your leave reason?",
        "submitted": "Application 11111111-1111-4111-8111-111111111111 submitted for 2026-11-16 to 2026-11-18.",
        "cancelled": "Leave application cancelled.", "ineligible": "Requested days exceed the available leave balance.",
        "error": "Employee API unavailable.", "submission_unknown": "Submission outcome unknown. Inspect saved applications before submitting again.",
        "ready": None, "confirmed": None,
    }
    return {
        "thread_id": thread, "conversation_id": conversation, "status": status,
        "message": messages[status], "interrupt": pending,
        "application": copy.deepcopy(RECEIPT) if status == "submitted" else None, "warnings": warnings or [],
    }


class FakeApi:
    def __init__(self):
        self.calls, self.urls = [], []
        self.saved = {}
        self.overrides = {key: [] for key in ("start", "get", "resume", "cancel")}
        self.counter = 0

    def client(self, url):
        self.urls.append(url)
        return self

    def _resolve(self, name, default, *, thread=None, conversation=None):
        value = self.overrides[name].pop(0) if self.overrides[name] else default
        if isinstance(value, Exception):
            raise value
        value = copy.deepcopy(value)
        if thread is not None:
            value["thread_id"] = thread
        if conversation is not None:
            value["conversation_id"] = conversation
        self.saved[value["thread_id"]] = copy.deepcopy(value)
        return value

    def start(self, request, conversation_id):
        self.calls.append(("start", request, conversation_id))
        self.counter += 1
        return self._resolve("start", workflow(), thread=f"thread-{self.counter}", conversation=conversation_id)

    def get(self, thread_id):
        self.calls.append(("get", thread_id))
        if not self.overrides["get"] and thread_id not in self.saved:
            raise ApiClientError("Workflow not found.", status_code=404, thread_id=thread_id)
        return self._resolve("get", self.saved.get(thread_id), thread=thread_id)

    def resume(self, thread_id, *, prompt_id=None, answer=None):
        self.calls.append(("resume", thread_id, prompt_id, answer))
        current = self.saved[thread_id]
        status = current["status"]
        if prompt_id and current["interrupt"]:
            status = "awaiting_confirmation" if current["interrupt"]["kind"] == "clarification" else ("submitted" if answer is True else "cancelled")
        return self._resolve("resume", workflow(status), thread=thread_id, conversation=current["conversation_id"])

    def cancel(self, thread_id):
        self.calls.append(("cancel", thread_id))
        return self._resolve("cancel", workflow("cancelled"), thread=thread_id, conversation=self.saved[thread_id]["conversation_id"])


class ClientTests(unittest.TestCase):
    def test_paths_bodies_boolean_and_timeouts(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=workflow())

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            api = WorkflowApiClient("http://localhost:8003/", client=http)
            api.start(" Vacation ", "conversation-1")
            api.get("thread-1")
            api.resume("thread-1", prompt_id="confirmation", answer=True)
            api.resume("thread-1")
            api.cancel("thread-1")
        self.assertEqual([request.url.path for request in requests], ["/workflows", "/workflows/thread-1", "/workflows/thread-1/resume", "/workflows/thread-1/resume", "/workflows/thread-1/cancel"])
        import json
        self.assertEqual(json.loads(requests[0].content), {"request": "Vacation", "conversation_id": "conversation-1"})
        self.assertIs(json.loads(requests[2].content)["answer"], True)
        self.assertEqual(json.loads(requests[3].content), {})
        self.assertEqual(requests[1].extensions["timeout"]["read"], 10)
        self.assertEqual(requests[0].extensions["timeout"]["read"], 600)

    def test_error_status_codes_and_validation_detail(self):
        for code in (404, 409, 422, 503):
            with self.subTest(code=code):
                detail = [{"msg": "Field required", "input": "do not echo raw input"}] if code == 422 else "Backend unavailable"
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(code, json={"detail": detail, "thread_id": "thread-1", "conversation_id": "conversation-1"}))) as http:
                    with self.assertRaises(ApiClientError) as caught:
                        WorkflowApiClient(client=http).resume("thread-1")
                self.assertEqual(caught.exception.status_code, code)
                self.assertEqual(caught.exception.thread_id, "thread-1")
                self.assertEqual(caught.exception.uncertain, code >= 500)
                self.assertNotIn("do not echo", str(caught.exception))

    def test_timeout_has_no_retry(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("private transport details", request=request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            with self.assertRaises(ApiClientError) as caught:
                WorkflowApiClient(client=http).resume("thread-1", prompt_id="confirmation", answer=True)
        self.assertEqual(len(calls), 1)
        self.assertTrue(caught.exception.uncertain)
        self.assertIn("timed out", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))

    def test_unavailable_api_and_unreadable_or_malformed_response(self):
        for response in (httpx.Response(200, text="<html>not an API</html>"), httpx.Response(200, json={"wrong": True}), httpx.Response(200, json=workflow(thread="another-thread"))):
            with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http:
                with self.assertRaises(ApiClientError):
                    WorkflowApiClient(client=http).get("thread-1")
        with httpx.Client(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request)))) as http:
            with self.assertRaises(ApiClientError) as caught:
                WorkflowApiClient(client=http).get("thread-1")
        self.assertFalse(caught.exception.uncertain)

    def test_validation_prevents_incomplete_review_and_bad_urls(self):
        invalid = workflow()
        invalid["interrupt"]["details"].pop("leave")
        with self.assertRaises(ValueError):
            validate_workflow(invalid)
        for url in ("not-a-url", "ftp://example.com", "http://user:password@example.com", "http://localhost:bad", "http://localhost?key=secret"):
            with self.assertRaises(ValueError):
                normalize_url(url)


class StateTests(unittest.TestCase):
    def test_busy_queue_and_consumed_action_do_not_repeat_posts(self):
        state, api = ui_state.fresh_state(), FakeApi()
        self.assertTrue(ui_state.queue_chat(state, "Vacation"))
        self.assertFalse(ui_state.queue_chat(state, "Duplicate"))
        ui_state.process_pending(state, api_factory=api.client)
        self.assertFalse(ui_state.process_pending(state, api_factory=api.client))
        self.assertEqual(len(api.calls), 1)
        self.assertFalse(ui_state.can_chat(state))
        self.assertTrue(ui_state.queue_confirmation(state, True))
        ui_state.process_pending(state, api_factory=api.client)
        ui_state.process_pending(state, api_factory=api.client)
        self.assertEqual(len([call for call in api.calls if call[0] == "resume"]), 1)
        self.assertEqual(len(state["messages"]), 4)

    def test_started_action_is_not_replayed_and_receipt_survives_error(self):
        state, api = ui_state.fresh_state(), FakeApi()
        ui_state.queue_chat(state, "Vacation")
        state["pending_action"]["started"] = True
        ui_state.process_pending(state, api_factory=api.client)
        self.assertEqual(api.calls, [])
        self.assertTrue(state["uncertain"])
        self.assertFalse(state["busy"])
        ui_state.apply_response(state, workflow("submitted"))
        ui_state.queue_action(state, "refresh", thread_id="thread-1")
        api.overrides["get"] = [ApiClientError("Storage unavailable", status_code=503)]
        ui_state.process_pending(state, api_factory=api.client)
        self.assertIsNotNone(state["workflow"]["application"])
        self.assertIn(RECEIPT["application_id"], state["receipts"])

    def test_get_responses_merge_chat_and_confirmation_requires_boolean(self):
        state = ui_state.fresh_state()
        response = workflow()
        ui_state.apply_response(state, response)
        ui_state.apply_response(state, response)
        self.assertEqual(len(state["messages"]), 1)
        self.assertFalse(ui_state.queue_chat(state, "yes"))
        self.assertFalse(ui_state.queue_confirmation(state, "yes"))
        self.assertTrue(ui_state.queue_confirmation(state, False))


class StreamlitTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.mock_api = patch.object(ui_state, "WorkflowApiClient", side_effect=self.api.client)
        self.mock_api.start()
        self.addCleanup(self.mock_api.stop)
        self.app = AppTest.from_file(str(UI_ROOT / "app.py"), default_timeout=10)

    def render(self):
        self.app.run()
        self.assertEqual(len(self.app.exception), 0, [str(item.value) for item in self.app.exception])
        return self.app

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def click(self, label):
        self.button(label).click().run()
        self.assertEqual(len(self.app.exception), 0, [str(item.value) for item in self.app.exception])

    def chat(self, text):
        self.app.chat_input[0].set_value(text).run()
        self.assertEqual(len(self.app.exception), 0, [str(item.value) for item in self.app.exception])

    def state(self):
        return self.app.session_state["leave_ui"]

    def test_initial_page_and_submission_review_receipt_and_reruns(self):
        self.render()
        self.assertEqual(self.api.calls, [])
        self.chat("Three days for vacation")
        self.assertEqual(self.state()["workflow"]["status"], "awaiting_confirmation")
        self.assertTrue(self.app.chat_input[0].disabled)
        self.assertEqual({item.label for item in self.app.metric}, {"Working days", "Available days", "Start date", "End date"})
        self.click("Submit application")
        self.assertEqual(self.state()["workflow"]["status"], "submitted")
        self.assertTrue(any(item.value == "Submission receipt" for item in self.app.subheader))
        self.assertTrue(any("submitted" in item.value.lower() for item in self.app.success))
        before = copy.deepcopy(self.api.calls)
        messages = len(self.state()["messages"])
        self.render()
        self.render()
        self.assertEqual(self.api.calls, before)
        self.assertEqual(len(self.state()["messages"]), messages)
        self.assertEqual([call[3] for call in self.api.calls if call[0] == "resume"], [True])

    def test_clarification_chat_then_decline(self):
        self.api.overrides["start"] = [workflow("clarifying")]
        self.render()
        self.chat("I need leave")
        self.assertFalse(self.app.chat_input[0].disabled)
        self.chat("Family vacation")
        self.assertEqual(self.api.calls[-1][2:], ("clarification:0", "Family vacation"))
        self.click("Decline")
        self.assertEqual(self.state()["workflow"]["status"], "cancelled")
        self.assertIs(self.api.calls[-1][3], False)

    def test_cancel_clarification(self):
        self.api.overrides["start"] = [workflow("clarifying")]
        self.render()
        self.chat("Leave")
        self.click("Cancel workflow")
        self.assertEqual(self.api.calls[-1][0], "cancel")
        self.assertEqual(self.state()["workflow"]["status"], "cancelled")

    def test_refresh_and_continue_are_different_actions(self):
        self.api.overrides["start"] = [workflow("ready")]
        self.render()
        self.chat("Leave")
        self.click("Refresh status")
        self.assertEqual(self.api.calls[-1][0], "get")
        self.click("Continue workflow")
        self.assertEqual(self.api.calls[-1], ("resume", "thread-1", None, None))

    def test_load_form_and_query_restore_are_read_only_and_deduplicated(self):
        self.api.saved["saved-thread"] = workflow(thread="saved-thread")
        self.app.query_params["thread_id"] = "saved-thread"
        self.render()
        self.assertEqual(self.api.calls, [("get", "saved-thread")])
        self.assertEqual(self.state()["thread_id"], "saved-thread")
        self.render()
        self.assertEqual(len(self.api.calls), 1)
        self.click("Refresh status")
        self.assertEqual(self.api.calls[-1], ("get", "saved-thread"))
        self.assertEqual(len(self.state()["messages"]), 1)
        self.app.text_input(key="workflow_id_input").set_value("saved-thread").run()
        self.click("Load workflow")
        self.assertTrue(all(call[0] == "get" for call in self.api.calls))
        self.assertEqual(len(self.state()["messages"]), 1)

    def test_restore_in_new_browser_session_uses_workflow_url(self):
        self.render()
        self.chat("Vacation")
        thread = self.state()["thread_id"]
        self.assertEqual(self.app.query_params["thread_id"], [thread])
        self.app = AppTest.from_file(str(UI_ROOT / "app.py"), default_timeout=10)
        self.app.query_params["thread_id"] = thread
        self.render()
        self.assertEqual(self.api.calls[-1], ("get", thread))
        self.assertEqual(self.state()["workflow"]["interrupt"]["kind"], "confirmation")

    def test_conversation_reuse_and_new_conversation(self):
        self.render()
        self.app.text_input(key="reuse_conversation_input").set_value("saved-conversation").run()
        self.click("Use conversation")
        self.assertEqual(self.api.calls, [])
        self.chat("Same reason as earlier")
        self.assertEqual(self.api.calls[-1][2], "saved-conversation")
        self.click("Decline")
        self.chat("Another request")
        self.assertEqual(self.api.calls[-1][2], "saved-conversation")
        before = len(self.api.calls)
        self.click("New conversation")
        self.assertNotEqual(self.state()["conversation_id"], "saved-conversation")
        self.assertIsNone(self.state()["thread_id"])
        self.assertEqual(len(self.api.calls), before)

    def test_stale_prompt_refreshes_without_reposting(self):
        self.render()
        self.chat("Vacation")
        thread = self.state()["thread_id"]
        self.api.saved[thread] = workflow("cancelled", thread=thread, conversation=self.state()["conversation_id"])
        self.api.overrides["resume"] = [ApiClientError("The prompt ID is stale.", status_code=409, thread_id=thread)]
        self.click("Submit application")
        self.assertEqual([call[0] for call in self.api.calls], ["start", "resume", "get"])
        self.assertEqual(self.state()["workflow"]["status"], "cancelled")
        self.assertTrue(any("stale" in item.value for item in self.app.error))
        self.render()
        self.assertEqual(len(self.api.calls), 3)

    def test_timeout_freezes_approval_until_read_only_refresh(self):
        self.render()
        self.chat("Vacation")
        thread = self.state()["thread_id"]
        self.api.overrides["resume"] = [ApiClientError("The API request timed out.", uncertain=True, thread_id=thread)]
        self.click("Submit application")
        self.assertTrue(self.state()["uncertain"])
        self.assertTrue(self.button("Submit application").disabled)
        calls = len(self.api.calls)
        self.render()
        self.assertEqual(len(self.api.calls), calls)
        self.click("Refresh status")
        self.assertEqual(self.api.calls[-1], ("get", thread))
        self.assertFalse(self.state()["uncertain"])
        self.assertEqual(len([call for call in self.api.calls if call[0] == "resume"]), 1)

    def test_statuses_warnings_and_receipt_survives_failed_refresh(self):
        self.api.overrides["start"] = [workflow("ineligible")]
        self.render()
        self.chat("Too many leave days")
        self.assertTrue(any("not eligible" in item.value.lower() for item in self.app.warning))
        self.api.overrides["start"] = [workflow("error")]
        self.chat("Try a new request")
        self.assertTrue(any("Employee API" in item.value for item in self.app.error))
        self.api.overrides["start"] = [workflow("awaiting_confirmation")]
        self.chat("Vacation")
        self.api.overrides["resume"] = [workflow("submitted", warnings=["Conversation storage failed after submission."])]
        self.click("Submit application")
        self.assertTrue(any("storage failed" in item.value for item in self.app.warning))
        self.api.overrides["get"] = [ApiClientError("Workflow storage unavailable", status_code=503)]
        self.click("Refresh status")
        self.assertIsNotNone(self.state()["workflow"]["application"])
        self.assertTrue(any(item.value == "Submission receipt" for item in self.app.subheader))

    def test_missing_thread_and_backend_connection_settings(self):
        self.render()
        self.app.text_input(key="workflow_id_input").set_value("missing").run()
        self.click("Load workflow")
        self.assertTrue(any("not found" in item.value for item in self.app.error))
        self.app.text_input(key="api_url_input").set_value("http://localhost:9000").run()
        self.click("Apply connection")
        self.assertEqual(self.state()["api_url"], "http://localhost:9000")
        self.assertIsNone(self.state()["thread_id"])
        self.chat("Vacation")
        self.assertEqual(self.api.urls[-1], "http://localhost:9000")

    def test_processing_disables_controls_and_unknown_outcome_does_not_retry(self):
        state = ui_state.fresh_state()
        state.update(busy=True, query_checked=True)
        self.app.session_state["leave_ui"] = state
        self.render()
        self.assertTrue(self.app.chat_input[0].disabled)
        self.assertTrue(all(button.disabled for button in self.app.button))
        self.assertEqual(self.api.calls, [])
        self.app.session_state["leave_ui"] = ui_state.fresh_state()
        self.api.overrides["start"] = [ApiClientError("The result was not received.", uncertain=True)]
        self.render()
        self.chat("Vacation")
        self.assertTrue(self.state()["uncertain"])
        self.assertTrue(self.app.chat_input[0].disabled)
        self.render()
        self.assertEqual(len(self.api.calls), 1)
        self.click("New conversation")
        self.assertFalse(self.app.chat_input[0].disabled)
        self.assertEqual(len(self.api.calls), 1)

    def test_submission_unknown_is_displayed_without_automatic_recovery(self):
        self.api.overrides["start"] = [workflow("submission_unknown")]
        self.render()
        self.chat("Check a request")
        self.assertTrue(any("outcome unknown" in item.value.lower() for item in self.app.warning))
        self.assertFalse(any(button.label == "Continue workflow" for button in self.app.button))
        self.render()
        self.assertEqual(len(self.api.calls), 1)


if __name__ == "__main__":
    unittest.main()
