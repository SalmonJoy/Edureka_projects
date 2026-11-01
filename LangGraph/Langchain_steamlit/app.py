"""Streamlit frontend for the existing FastAPI leave workflow."""

from pathlib import Path
import sys

UI_ROOT = Path(__file__).resolve().parent
if str(UI_ROOT) not in sys.path:
    sys.path.insert(0, str(UI_ROOT))

import streamlit as st

from api_client import normalize_url
import ui_state as model


st.set_page_config(page_title="Leave Assistant", layout="wide", initial_sidebar_state="expanded")


def current():
    return st.session_state["leave_ui"]


def clear_query():
    if "thread_id" in st.query_params:
        del st.query_params["thread_id"]


def new_conversation():
    previous = current()
    if previous["busy"]:
        return
    st.session_state["leave_ui"] = model.fresh_state(previous["api_url"])
    current()["query_checked"] = True
    clear_query()


def use_conversation():
    value = st.session_state.get("reuse_conversation_input", "").strip()
    if not value:
        current()["error"] = "Enter a conversation ID."
        return
    if current()["busy"]:
        return
    url = current()["api_url"]
    st.session_state["leave_ui"] = model.fresh_state(url, conversation_id=value)
    current().update(query_checked=True, notice="Conversation selected. Earlier messages remain on the backend and will be used for new requests.")
    clear_query()


def apply_connection():
    if current()["busy"]:
        return
    try:
        url = normalize_url(st.session_state.get("api_url_input", ""))
    except ValueError as error:
        current()["error"] = str(error)
        return
    if url != current()["api_url"]:
        st.session_state["leave_ui"] = model.fresh_state(url)
        current()["query_checked"] = True
        clear_query()
    current()["notice"] = "API connection URL updated."


def load_workflow():
    value = st.session_state.get("workflow_id_input", "").strip()
    if not value:
        current()["error"] = "Enter a workflow ID."
        return
    model.queue_action(current(), "load", thread_id=value)


def queue_existing(kind):
    state = current()
    if state["thread_id"]:
        model.queue_action(state, kind, thread_id=state["thread_id"])


def send_chat():
    model.queue_chat(current(), st.session_state.get("leave_chat", ""))


def confirm(approved):
    model.queue_confirmation(current(), approved)


def sidebar(state):
    with st.sidebar:
        st.title("Leave Assistant")
        st.caption("Conversations and saved workflows")
        st.button("New conversation", on_click=new_conversation, disabled=state["busy"], use_container_width=True)
        st.caption("Starting a new chat leaves previous workflows saved on the backend.")
        st.markdown("**Conversation ID**")
        st.code(state["conversation_id"], language=None)
        if state["thread_id"]:
            st.markdown("**Workflow ID**")
            st.code(state["thread_id"], language=None)
        with st.form("load_workflow_form"):
            st.text_input("Saved workflow ID", key="workflow_id_input", disabled=state["busy"])
            st.form_submit_button("Load workflow", on_click=load_workflow, disabled=state["busy"])
        st.button("Refresh status", on_click=queue_existing, args=("refresh",),
                  disabled=state["busy"] or not state["thread_id"], use_container_width=True)
        workflow = state.get("workflow")
        pending = model.pending_prompt(state)
        if workflow and not pending and workflow["status"] not in {"cancelled", "ineligible", "error", "submission_unknown"}:
            st.button("Continue workflow", on_click=queue_existing, args=("continue",),
                      disabled=state["busy"] or state["uncertain"], use_container_width=True)
            st.caption("Recovers safe unfinished work or returns an already saved result.")
        if workflow and workflow["status"] not in model.TERMINAL_STATUSES and (not pending or pending["kind"] != "confirmation"):
            st.button("Cancel workflow", on_click=queue_existing, args=("cancel",),
                      disabled=state["busy"] or state["uncertain"], use_container_width=True)
        with st.expander("Reuse a conversation"):
            with st.form("reuse_conversation_form"):
                st.text_input("Existing conversation ID", key="reuse_conversation_input", disabled=state["busy"])
                st.form_submit_button("Use conversation", on_click=use_conversation, disabled=state["busy"])
            st.caption("The API uses earlier saved messages as context. It does not return the full transcript to this UI.")
        with st.expander("API connection"):
            with st.form("api_connection_form"):
                st.text_input("FastAPI URL", value=state["api_url"], key="api_url_input", disabled=state["busy"])
                st.form_submit_button("Apply connection", on_click=apply_connection, disabled=state["busy"])


def review_panel(state, pending):
    details = pending["details"]
    employee, leave = details["employee"], details["leave"]
    with st.container(border=True):
        st.subheader("Review your leave")
        st.caption(f"{employee.get('name', '')} · {employee.get('employee_id', '')} · {leave['leave_type'].title()} leave")
        days, balance, start, end = st.columns(4)
        days.metric("Working days", details["requested_days"])
        balance.metric("Available days", details["leave_balance"])
        start.metric("Start date", leave["start_date"])
        end.metric("End date", leave["end_date"])
        st.markdown("**Reason**")
        st.write(leave["reason"])
        st.markdown("**Policy eligibility**")
        for reason in details["eligibility"]["reasons"]:
            st.write("• " + reason)
        references = details["eligibility"].get("supporting_chunk_ids", [])
        if references:
            st.caption("Policy references: " + ", ".join(str(item) for item in references))
        with st.expander("Employee details"):
            for label, key in [("Email", "email"), ("Department", "department"), ("Designation", "designation"),
                               ("Joined", "joining_date"), ("Manager ID", "manager_id")]:
                st.write(f"{label}: {employee.get(key) or '—'}")
        st.caption("Submitting records an application. Manager approval is a later step.")
        approve, decline = st.columns(2)
        disabled = state["busy"] or state["uncertain"]
        key = state["thread_id"] + ":" + pending["prompt_id"]
        approve.button("Submit application", type="primary", key="approve:" + key,
                       on_click=confirm, args=(True,), disabled=disabled, use_container_width=True)
        decline.button("Decline", key="decline:" + key,
                       on_click=confirm, args=(False,), disabled=disabled, use_container_width=True)


def receipt_panel(application):
    with st.container(border=True):
        st.subheader("Submission receipt")
        st.write("Application ID")
        st.code(application["application_id"], language=None)
        st.write(f"Leave: {application['leave']['start_date']} to {application['leave']['end_date']}")
        if application.get("submitted_at"):
            st.caption("Submitted at: " + application["submitted_at"])
        with st.expander("Receipt details"):
            st.json(application)


def main():
    if "leave_ui" not in st.session_state:
        st.session_state["leave_ui"] = model.fresh_state()
    state = current()
    if not state["query_checked"]:
        state["query_checked"] = True
        thread_id = st.query_params.get("thread_id", "").strip()
        if thread_id:
            model.queue_action(state, "load", thread_id=thread_id)
    sidebar(state)
    st.title("Plan your leave")
    st.caption("Tell us your dates and reason. Review eligibility, then decide whether to submit.")
    if state["error"]:
        st.error(state["error"])
    if state["notice"]:
        st.info(state["notice"])
    if state["uncertain"]:
        st.warning("Check saved status before another action. If no workflow ID was received, load it manually once known; the previous action was not automatically retried.")
    workflow = state.get("workflow")
    if workflow:
        labels = {
            "clarifying": "Needs more details", "awaiting_confirmation": "Waiting for your confirmation",
            "submitted": "Application submitted", "cancelled": "Cancelled", "ineligible": "Not eligible",
            "error": "Could not complete", "submission_unknown": "Submission outcome unknown",
            "understanding": "Understanding request", "ready": "Checking request", "confirmed": "Submission state needs checking",
        }
        st.caption("Workflow status: " + labels.get(workflow["status"], workflow["status"]))
    if not state["messages"] and not state["busy"]:
        st.info("Describe when you would like leave and why. You can also load a saved workflow from the sidebar.")
    for message in state["messages"]:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    pending = model.pending_prompt(state)
    if pending and pending["kind"] == "confirmation":
        review_panel(state, pending)
    if workflow:
        if workflow["status"] == "submitted":
            st.success("Your leave application was submitted.")
        elif workflow["status"] == "ineligible":
            st.warning("This request is not eligible. See the explanation above.")
        elif workflow["status"] == "cancelled":
            st.info("This workflow was cancelled.")
        elif workflow["status"] == "error":
            st.error(workflow.get("message") or "The workflow could not complete.")
        elif workflow["status"] == "submission_unknown":
            st.warning(workflow.get("message") or "Check saved leave applications before submitting again.")
        for warning in workflow.get("warnings", []):
            st.warning(warning)
        if workflow.get("application"):
            receipt_panel(workflow["application"])
    active_application = (workflow or {}).get("application")
    previous = [receipt for key, receipt in state["receipts"].items()
                if not active_application or key != active_application["application_id"]]
    if previous:
        with st.expander("Previous submission receipts"):
            for receipt in previous:
                st.json(receipt)
    placeholder = "Answer the clarification question…" if pending and pending["kind"] == "clarification" else "Describe your leave dates and reason…"
    st.chat_input(placeholder, key="leave_chat", on_submit=send_chat, disabled=not model.can_chat(state))
    if state["pending_action"] is not None:
        with st.spinner("Processing your request. Waiting for the workflow API…"):
            changed = model.process_pending(state)
        if changed:
            if state["thread_id"]:
                st.query_params["thread_id"] = state["thread_id"]
            else:
                clear_query()
            st.rerun()


main()
