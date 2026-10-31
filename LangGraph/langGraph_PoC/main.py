"""Run with python main.py from this folder, or python -m langGraph_PoC.main."""

import asyncio
import json
from pathlib import Path
import sys
from uuid import uuid4

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command

from langGraph_PoC.clients import WorkflowClients
from langGraph_PoC.config import load_settings
from langGraph_PoC.workflow import build_graph, initial_state


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


async def run_request(graph, request: str):
    config = {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 100}
    print("Thread:", config["configurable"]["thread_id"])
    graph_input = initial_state(request)
    while True:
        async for update in graph.astream(graph_input, config, stream_mode="updates"):
            for node in update:
                if node != "__interrupt__":
                    print(f"  {NODE_LABELS.get(node, node)} completed")
        snapshot = await graph.aget_state(config)
        pending = [item for task in snapshot.tasks for item in task.interrupts]
        if not pending:
            message = snapshot.values.get("final_message", "The workflow finished without a message.")
            print("\n" + message + "\n")
            return snapshot.values
        payload = pending[0].value
        if payload["kind"] == "clarification":
            print("\n" + payload["question"])
            answer = input("Your answer (or /cancel): ").strip()
            if answer.lower() == "/cancel":
                print("Request cancelled.\n")
                return snapshot.values
            while not answer:
                answer = input("Please provide a nonblank answer (or /cancel): ").strip()
                if answer.lower() == "/cancel":
                    print("Request cancelled.\n")
                    return snapshot.values
        else:
            print("\nReview before submission:")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            answer = confirmation_input()
        graph_input = Command(resume=answer)


async def main():
    try:
        settings = load_settings()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    clients = WorkflowClients(settings)
    graph = build_graph(clients)
    print("LangGraph leave application demo")
    print(f"Employee: {settings.employee_id}; model: {settings.ollama_model}")
    print("Enter a leave request, or /quit. Confirmation is required before submission.\n")
    try:
        while True:
            request = input("Leave request: ").strip()
            if request.lower() in ("/quit", "quit", "exit"):
                return 0
            if not request:
                continue
            await run_request(graph, request)
    finally:
        await clients.aclose()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (EOFError, KeyboardInterrupt):
        print("\nSession ended. Check any submission already in progress before trying again.")
