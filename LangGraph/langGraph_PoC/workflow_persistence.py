"""SQLite checkpoint lifecycle and safe workflow recovery helpers."""

from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


CHECKPOINT_FILE = Path(__file__).resolve().parent / "workflow_state.sqlite"


class WorkflowPersistenceError(RuntimeError):
    """A checkpoint or recovery error suitable for terminal output."""


@asynccontextmanager
async def open_checkpointer(path: Path = CHECKPOINT_FILE):
    """Keep the connection open for the compiled graph's entire lifetime."""
    try:
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            await saver.setup()
            yield saver
    except (OSError, sqlite3.Error):
        raise WorkflowPersistenceError(
            "Cannot read or write SQLite workflow state. Check the database path, "
            "file permissions, and whether another process is using it. "
            "The database was not reset and no workflow retry was made."
        ) from None


def thread_config(thread_id: str) -> dict:
    if not thread_id or not thread_id.strip():
        raise WorkflowPersistenceError("A nonblank workflow thread ID is required.")
    from langGraph_PoC.tracing import tracing_config

    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100,
            "metadata": {"thread_id": thread_id}, **tracing_config()}


async def load_thread(graph, thread_id: str, *, conversation_id: str | None = None):
    snapshot = await graph.aget_state(thread_config(thread_id))
    if not snapshot.values:
        raise WorkflowPersistenceError(f"No saved workflow found for thread {thread_id}.")
    saved_conversation = snapshot.values.get("conversation_id")
    if conversation_id and saved_conversation and conversation_id != saved_conversation:
        raise WorkflowPersistenceError("The conversation ID does not match this saved workflow.")
    if "apply_leave" in snapshot.next and not snapshot.values.get("application"):
        raise WorkflowPersistenceError(
            "This workflow stopped at leave submission without a saved receipt. Its outcome "
            "may be unknown. Check leave_apply/leave_applications.jsonl before submitting "
            "again; the pending POST was not repeated."
        )
    return snapshot


def saved_conversation_id(snapshot, requested: str | None = None) -> str:
    saved = snapshot.values.get("conversation_id")
    if requested and saved and requested != saved:
        raise WorkflowPersistenceError("The conversation ID does not match this saved workflow.")
    if not saved and not requested:
        raise WorkflowPersistenceError(
            "This saved workflow has no conversation ID. Supply --conversation-id to associate its transcript."
        )
    return saved or requested


def prompt_event_id(snapshot, payload: dict) -> str:
    """Checkpoint edits may change interrupt IDs; transcript event IDs stay stable."""
    if payload["kind"] == "confirmation":
        return "confirmation"
    return f"clarification:{len(snapshot.values.get('clarification_history', []))}"


async def cancel_workflow(graph, config: dict):
    """Close a clarification's pending path so cancellation also survives restart."""
    await graph.aupdate_state(
        config,
        {"status": "cancelled", "confirmed": False, "clarification_question": None,
         "final_message": "Request cancelled."},
        as_node="understand_request",
    )
