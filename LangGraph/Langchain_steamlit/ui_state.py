"""Session transitions and one-shot actions, independent of Streamlit rendering."""

from uuid import uuid4

from api_client import ApiClientError, DEFAULT_API_URL, WorkflowApiClient


TERMINAL_STATUSES = {"submitted", "cancelled", "ineligible", "error", "submission_unknown"}


def fresh_state(api_url=DEFAULT_API_URL, *, conversation_id=None):
    return {
        "api_url": api_url, "conversation_id": conversation_id or str(uuid4()),
        "thread_id": None, "workflow": None, "messages": [], "receipts": {},
        "error": None, "notice": None, "busy": False, "pending_action": None,
        "uncertain": False, "query_checked": False,
    }


def pending_prompt(state):
    return (state.get("workflow") or {}).get("interrupt")


def can_chat(state):
    if state["busy"] or state["uncertain"]:
        return False
    pending = pending_prompt(state)
    return pending["kind"] == "clarification" if pending else (
        state["workflow"] is None or state["workflow"]["status"] in TERMINAL_STATUSES
    )


def add_message(state, message_id, role, content):
    for message in state["messages"]:
        if message["id"] == message_id:
            message.update(role=role, content=content)
            return
    state["messages"].append({"id": message_id, "role": role, "content": content})


def apply_response(state, response):
    if response["conversation_id"] != state["conversation_id"]:
        state["messages"] = []
    state.update(
        workflow=response, thread_id=response["thread_id"],
        conversation_id=response["conversation_id"], uncertain=False,
    )
    pending = response.get("interrupt")
    text = response.get("message")
    suffix = "prompt:" + pending["prompt_id"] if pending else "result:" + response["status"]
    if pending and pending["kind"] == "confirmation":
        text = "Your request is ready for review. Check the leave details and policy reasons before submitting."
    if text:
        add_message(state, response["thread_id"] + ":" + suffix, "assistant", text)
    if response.get("application"):
        receipt = response["application"]
        state["receipts"][receipt["application_id"]] = receipt
    if response["status"] == "confirmed" and not pending and not response.get("application"):
        state["uncertain"] = True


def queue_action(state, kind, **payload):
    if state["busy"] or state["pending_action"] is not None:
        return False
    state["busy"] = True
    state["error"] = None
    state["notice"] = None
    state["pending_action"] = {"id": str(uuid4()), "kind": kind, "payload": payload, "started": False}
    return True


def queue_chat(state, text):
    if not isinstance(text, str) or not text.strip() or not can_chat(state):
        return False
    pending = pending_prompt(state)
    if pending:
        return queue_action(state, "answer", thread_id=state["thread_id"], prompt_id=pending["prompt_id"], answer=text.strip())
    return queue_action(state, "start", request=text.strip())


def queue_confirmation(state, approved: bool):
    pending = pending_prompt(state)
    if type(approved) is not bool or state["uncertain"] or not pending or pending["kind"] != "confirmation":
        return False
    return queue_action(state, "answer", thread_id=state["thread_id"], prompt_id=pending["prompt_id"], answer=approved)


def process_pending(state, *, api_factory=None):
    action = state["pending_action"]
    if action is None:
        return False
    if action["started"]:
        state.update(busy=False, pending_action=None, uncertain=True,
                     error="The previous action was interrupted. Refresh saved status before another action; it was not retried.")
        return True
    action["started"] = True  # Set before network I/O: a rerun cannot repeat this action.
    kind, payload = action["kind"], action["payload"]
    completed = False
    try:
        api = (api_factory or WorkflowApiClient)(state["api_url"])
        if kind == "start":
            add_message(state, "user:" + action["id"], "user", payload["request"])
            state["workflow"] = None
            state["thread_id"] = None
            response = api.start(payload["request"], state["conversation_id"])
        elif kind == "answer":
            answer = payload["answer"]
            text = ("Submit this application." if answer else "Decline this application.") if type(answer) is bool else answer
            add_message(state, "user:" + action["id"], "user", text)
            response = api.resume(payload["thread_id"], prompt_id=payload["prompt_id"], answer=answer)
        elif kind in ("load", "refresh"):
            response = api.get(payload["thread_id"])
        elif kind == "continue":
            response = api.resume(payload["thread_id"])
        elif kind == "cancel":
            response = api.cancel(payload["thread_id"])
            add_message(state, "user:" + action["id"], "user", "Cancel this workflow.")
        else:
            raise ValueError("Unknown UI action")
        apply_response(state, response)
        completed = True
    except ApiClientError as error:
        state["error"] = str(error)
        if kind == "start" and error.thread_id:
            state["thread_id"] = error.thread_id
        if error.conversation_id and kind == "start":
            state["conversation_id"] = error.conversation_id
        state["uncertain"] = state["uncertain"] or error.uncertain
        if error.status_code == 409 and state["thread_id"]:
            # Refresh is read-only. Never replay the failed answer or recovery POST.
            try:
                apply_response(state, api.get(state["thread_id"]))
                state["notice"] = "Saved status refreshed. Review it before taking another action."
            except ApiClientError:
                state["uncertain"] = True
        completed = True
    except ValueError as error:
        state["error"] = str(error)
        completed = True
    finally:
        if not completed:
            state["uncertain"] = True
            state["error"] = "The operation was interrupted. Check saved status before another action; it was not automatically retried."
        state["busy"] = False
        state["pending_action"] = None
    return True
