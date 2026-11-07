"""Run with python main.py from this folder, or python -m langGraph_PoC.main."""

import asyncio
import argparse
from pathlib import Path
import sys
from uuid import uuid4

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command

from langGraph_PoC.clients import WorkflowClients
from langGraph_PoC.config import load_settings
from langGraph_PoC.conversation_memory import ConversationMemory, ConversationStorageError, review_text
from langGraph_PoC.workflow import build_graph, initial_state
from langGraph_PoC.tracing import workflow_trace
from langGraph_PoC.workflow_persistence import (
    WorkflowPersistenceError, cancel_workflow, load_thread, open_checkpointer,
    prompt_event_id, saved_conversation_id, thread_config,
)


NODE_LABELS = {
    "understand_request": "1. Understand request",
    "fetch_employee": "2. Fetch employee",
    "fetch_policies": "3. Fetch HR policies",
    "check_eligibility": "4. Check eligibility",
    "human_review": "5. Human review",
    "explain_ineligibility": "6. Explain ineligibility",
    "apply_leave": "7. Submit application",
    "report_success": "8. Report success",
}


def confirmation_input() -> bool:
    while True:
        answer = input("Submit this application? [yes/no]: ").strip().lower()
        if answer in ("yes", "y"):
            return True
        if answer in ("no", "n"):
            return False
        print("Please enter yes or no.")


async def run_request(graph, request: str | None = None, *, memory=None, thread_id: str | None = None):
    config = thread_config(thread_id if thread_id is not None else str(uuid4()))
    state = initial_state(request) if thread_id is None and request is not None else None
    if state is not None and memory is not None:
        state["conversation_id"] = memory.conversation_id
    async with workflow_trace(graph, config, state):
        return await _run_request(graph, request, memory=memory, thread_id=thread_id,
                                  config=config, state=state)


async def _run_request(graph, request, *, memory, thread_id, config, state):
    print("Thread:", config["configurable"]["thread_id"])
    skip_stream = False
    if thread_id is not None:
        snapshot = await load_thread(
            graph, thread_id, conversation_id=memory.conversation_id if memory is not None else None,
        )
        graph_input = None  # Continue the saved state, without adding a new request.
        skip_stream = not snapshot.next or any(task.interrupts for task in snapshot.tasks)
    else:
        if request is None:
            raise ValueError("A leave request is required for a new workflow.")
        graph_input = state if state is not None else initial_state(request)
        if memory is not None:
            graph_input.update(await memory.start_request(request, config["configurable"]["thread_id"]))
    while True:
        if not skip_stream:
            async for update in graph.astream(graph_input, config, stream_mode="updates", durability="sync"):
                for node in update:
                    if node != "__interrupt__":
                        print(f"  {NODE_LABELS.get(node, node)} completed")
        skip_stream = False
        snapshot = await graph.aget_state(config)
        pending = [item for task in snapshot.tasks for item in task.interrupts]
        if not pending:
            message = snapshot.values.get("final_message", "The workflow finished without a message.")
            print("\n" + message + "\n")
            if memory is not None:
                # Display the receipt before attempting a transcript write that could fail.
                await memory.record(graph, config, "assistant", message, "final")
            return (await graph.aget_state(config)).values
        payload = pending[0].value
        event_id = prompt_event_id(snapshot, payload)
        prompt = payload["question"] if payload["kind"] == "clarification" else review_text(payload)
        if memory is not None:
            await memory.record(graph, config, "assistant", prompt, event_id + ":prompt")
        if payload["kind"] == "clarification":
            print("\n" + payload["question"])
            answer = input("Your answer (or /cancel): ").strip()
            if answer.lower() == "/cancel":
                print("Request cancelled.\n")
                await cancel_workflow(graph, config)
                if memory is not None:
                    await memory.record(graph, config, "user", "/cancel", event_id + ":answer")
                    await memory.record(graph, config, "assistant", "Request cancelled.", "final")
                return (await graph.aget_state(config)).values
            while not answer:
                answer = input("Please provide a nonblank answer (or /cancel): ").strip()
                if answer.lower() == "/cancel":
                    print("Request cancelled.\n")
                    await cancel_workflow(graph, config)
                    if memory is not None:
                        await memory.record(graph, config, "user", "/cancel", event_id + ":answer")
                        await memory.record(graph, config, "assistant", "Request cancelled.", "final")
                    return (await graph.aget_state(config)).values
        else:
            print("\nReview before submission:")
            print(prompt)
            answer = confirmation_input()
        if memory is not None:
            text = ("yes" if answer else "no") if type(answer) is bool else answer
            await memory.record(graph, config, "user", text, event_id + ":answer")
        graph_input = Command(resume=answer)


async def main():
    parser = argparse.ArgumentParser(description="LangGraph leave application demo")
    parser.add_argument("--conversation-id", help="Reopen a saved conversation transcript")
    parser.add_argument("--resume-thread", help="Resume a workflow saved in SQLite using its thread ID")
    args = parser.parse_args()
    clients = memory = None
    try:
        settings = load_settings()
        async with open_checkpointer() as checkpointer:
            clients = WorkflowClients(settings)
            graph = build_graph(clients, checkpointer=checkpointer)
            conversation_id = args.conversation_id
            if args.resume_thread is not None:
                snapshot = await load_thread(graph, args.resume_thread, conversation_id=conversation_id)
                conversation_id = saved_conversation_id(snapshot, conversation_id)
            memory = await ConversationMemory.connect(conversation_id)
            print("LangGraph leave application demo")
            print(f"Employee: {settings.employee_id}; model: {settings.ollama_model}")
            print(f"Conversation: {memory.conversation_id}")
            print("Enter a leave request, or /quit. Confirmation is required before submission.\n")
            if args.resume_thread is not None:
                await run_request(graph, memory=memory, thread_id=args.resume_thread)
            while True:
                request = input("Leave request: ").strip()
                if request.lower() in ("/quit", "quit", "exit"):
                    return 0
                if not request:
                    continue
                try:
                    await run_request(graph, request, memory=memory)
                except ConversationStorageError as error:
                    print(str(error), file=sys.stderr)
    except (ValueError, ConversationStorageError, WorkflowPersistenceError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if clients is not None:
            await clients.aclose()
        if memory is not None:
            await memory.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (EOFError, KeyboardInterrupt):
        print("\nSession ended. Check any submission already in progress before trying again.")
