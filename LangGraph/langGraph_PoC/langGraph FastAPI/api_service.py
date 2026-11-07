"""HTTP orchestration around the existing graph; no terminal input or graph edits."""

import asyncio
from contextlib import asynccontextmanager
import sqlite3
from uuid import uuid4

from pymongo.errors import PyMongoError
from langgraph.types import Command

from langGraph_PoC.conversation_memory import ConversationStorageError, review_text
from langGraph_PoC.workflow import initial_state
from langGraph_PoC.tracing import workflow_trace
from langGraph_PoC.workflow_persistence import (
    WorkflowPersistenceError, cancel_workflow, load_thread,
    prompt_event_id, thread_config,
)

from api_models import ResumeWorkflow, StartWorkflow, WorkflowInterrupt, WorkflowResponse


STORAGE_ERRORS = (ConversationStorageError, WorkflowPersistenceError, sqlite3.Error, PyMongoError)


class ApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str, *, thread_id=None, conversation_id=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.thread_id = thread_id
        self.conversation_id = conversation_id


class WorkflowService:
    def __init__(self, graph, memory_factory):
        self.graph = graph
        self.memory_factory = memory_factory
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def _conversation(self, conversation_id: str):
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        if lock.locked():
            raise ApiError(409, "Another modifying request is running for this conversation.")
        await lock.acquire()
        memory = None
        try:
            # Connect/load only after acquiring the lock so the MongoDB sequence is fresh.
            memory = await self.memory_factory(conversation_id)
            yield memory
        finally:
            try:
                if memory is not None:
                    await memory.close()
            finally:
                lock.release()

    async def _snapshot(self, thread_id: str):
        snapshot = await self.graph.aget_state(thread_config(thread_id))
        if not snapshot.values:
            raise ApiError(404, "Workflow not found.", thread_id=thread_id)
        return snapshot

    @staticmethod
    def _conversation_id(snapshot) -> str:
        conversation_id = snapshot.values.get("conversation_id")
        if not conversation_id:
            raise ApiError(409, "The saved workflow has no associated conversation.")
        return conversation_id

    @staticmethod
    def _pending(snapshot) -> dict | None:
        # Recording a transcript message can replace a task's interrupt metadata.
        # The existing node state still describes exactly the same saved prompt.
        for task in snapshot.tasks:
            for item in task.interrupts:
                return item.value
        state = snapshot.values
        if "understand_request" in snapshot.next and state.get("clarification_question"):
            return {
                "kind": "clarification", "question": state["clarification_question"],
                "known_details": state.get("extracted_request", {}),
            }
        if "human_review" in snapshot.next and state.get("status") == "awaiting_confirmation":
            return {
                "kind": "confirmation", "question": "Submit this leave application?",
                "employee": state["employee"], "leave": state["leave"],
                "requested_days": state["requested_days"],
                "leave_balance": state["employee"]["leave_balance"],
                "eligibility": state["eligibility"],
                "validation_error": state.get("confirmation_error"),
            }
        return None

    @staticmethod
    def _message(snapshot) -> str | None:
        state = snapshot.values
        if state.get("final_message"):
            return state["final_message"]
        if state.get("application"):
            record = state["application"]
            return (
                f"Leave application {record['application_id']} submitted for "
                f"{record['leave']['start_date']} to {record['leave']['end_date']}."
            )
        return state.get("error")

    def _response(self, snapshot, thread_id: str, *, warnings=None) -> WorkflowResponse:
        pending = self._pending(snapshot)
        interrupt = None
        message = self._message(snapshot)
        if pending is not None:
            interrupt = WorkflowInterrupt(
                prompt_id=prompt_event_id(snapshot, pending), kind=pending["kind"],
                question=pending["question"],
                details={key: value for key, value in pending.items() if key not in ("kind", "question")},
            )
            message = pending["question"] if pending["kind"] == "clarification" else review_text(pending)
        return WorkflowResponse(
            thread_id=thread_id, conversation_id=self._conversation_id(snapshot),
            status=snapshot.values.get("status", "understanding"), message=message,
            interrupt=interrupt, application=snapshot.values.get("application"),
            warnings=warnings or [],
        )

    async def _storage_failure(self, thread_id: str, conversation_id: str | None = None):
        try:
            snapshot = await self.graph.aget_state(thread_config(thread_id))
            if snapshot.values.get("application"):
                return self._response(snapshot, thread_id, warnings=[
                    "Conversation/checkpoint storage failed after a saved submission. "
                    "The saved receipt is included; the leave POST was not repeated."
                ])
        except STORAGE_ERRORS:
            pass
        raise ApiError(
            503, "Workflow storage is unavailable. No automatic retry was made. "
            "Check the saved workflow and leave JSONL file before retrying a submission.",
            thread_id=thread_id, conversation_id=conversation_id,
        ) from None

    async def _recoverable(self, thread_id: str, conversation_id: str):
        # Reuse the original helper's protection against ambiguous leave POST replay.
        try:
            return await load_thread(self.graph, thread_id, conversation_id=conversation_id)
        except WorkflowPersistenceError as error:
            raise ApiError(409, str(error), thread_id=thread_id, conversation_id=conversation_id) from None

    async def _advance(self, graph_input, config):
        await self.graph.ainvoke(graph_input, config, durability="sync")

    async def _boundary(self, config, memory) -> WorkflowResponse:
        thread_id = config["configurable"]["thread_id"]
        snapshot = await self._snapshot(thread_id)
        pending = self._pending(snapshot)
        if pending is not None:
            event_id = prompt_event_id(snapshot, pending)
            prompt = pending["question"] if pending["kind"] == "clarification" else review_text(pending)
            await memory.record(self.graph, config, "assistant", prompt, event_id + ":prompt")
        elif not snapshot.next:
            message = self._message(snapshot)
            if message:
                await memory.record(self.graph, config, "assistant", message, "final")
        return self._response(await self._snapshot(thread_id), thread_id)

    async def start(self, body: StartWorkflow) -> WorkflowResponse:
        thread_id = str(uuid4())
        conversation_id = body.conversation_id or str(uuid4())
        config = thread_config(thread_id)
        try:
            async with self._conversation(conversation_id) as memory:
                state = initial_state(body.request)
                state.update(await memory.start_request(body.request, thread_id))
                async with workflow_trace(self.graph, config, state):
                    await self._advance(state, config)
                    return await self._boundary(config, memory)
        except STORAGE_ERRORS:
            return await self._storage_failure(thread_id, conversation_id)

    async def get(self, thread_id: str) -> WorkflowResponse:
        try:
            return self._response(await self._snapshot(thread_id), thread_id)
        except STORAGE_ERRORS:
            raise ApiError(503, "Cannot read workflow storage.", thread_id=thread_id) from None

    async def resume(self, thread_id: str, body: ResumeWorkflow) -> WorkflowResponse:
        conversation_id = None
        try:
            snapshot = await self._snapshot(thread_id)
            conversation_id = self._conversation_id(snapshot)
            config = thread_config(thread_id)
            async with self._conversation(conversation_id) as memory, workflow_trace(self.graph, config):
                snapshot = await self._recoverable(thread_id, conversation_id)
                if not snapshot.next:
                    if body.prompt_id is not None:
                        raise ApiError(409, "This workflow is complete and is not awaiting an answer.")
                    return await self._boundary(config, memory)
                pending = self._pending(snapshot)
                if pending is not None:
                    if body.prompt_id is None:
                        return await self._boundary(config, memory)
                    event_id = prompt_event_id(snapshot, pending)
                    if body.prompt_id != event_id:
                        raise ApiError(409, "The prompt ID is stale or does not match the current prompt.")
                    if pending["kind"] == "confirmation" and type(body.answer) is not bool:
                        raise ApiError(422, "Confirmation requires a JSON Boolean answer.")
                    if pending["kind"] == "clarification" and not isinstance(body.answer, str):
                        raise ApiError(422, "Clarification requires a nonblank text answer.")
                    if not any(task.interrupts for task in snapshot.tasks):
                        # Reach the original interrupt before delivering the supplied answer.
                        await self._advance(None, config)
                        snapshot = await self._snapshot(thread_id)
                        current = self._pending(snapshot)
                        if current is None or prompt_event_id(snapshot, current) != event_id:
                            raise ApiError(409, "The workflow prompt changed; retrieve it again.")
                    text = ("yes" if body.answer else "no") if type(body.answer) is bool else body.answer
                    await memory.record(self.graph, config, "user", text, event_id + ":answer")
                    await self._advance(Command(resume=body.answer), config)
                else:
                    if body.prompt_id is not None:
                        raise ApiError(409, "The workflow is not awaiting an answer.")
                    await self._advance(None, config)
                return await self._boundary(config, memory)
        except STORAGE_ERRORS:
            return await self._storage_failure(thread_id, conversation_id)

    async def cancel(self, thread_id: str) -> WorkflowResponse:
        conversation_id = None
        try:
            snapshot = await self._snapshot(thread_id)
            conversation_id = self._conversation_id(snapshot)
            config = thread_config(thread_id)
            async with self._conversation(conversation_id) as memory, workflow_trace(self.graph, config):
                snapshot = await self._recoverable(thread_id, conversation_id)
                if snapshot.values.get("status") == "cancelled" and not snapshot.next:
                    return await self._boundary(config, memory)
                if not snapshot.next or snapshot.values.get("application"):
                    raise ApiError(409, "This workflow is complete and cannot be cancelled.")
                pending = self._pending(snapshot)
                event_id = prompt_event_id(snapshot, pending) if pending else "cancel"
                await cancel_workflow(self.graph, config)
                await memory.record(self.graph, config, "user", "/cancel", event_id + ":answer")
                return await self._boundary(config, memory)
        except STORAGE_ERRORS:
            return await self._storage_failure(thread_id, conversation_id)
