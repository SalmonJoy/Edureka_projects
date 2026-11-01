"""Run from this folder: python -m uvicorn app:app --reload --port 8003."""

from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
import sqlite3
import sys

# This folder contains a space; import the original package through its workspace root.
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from langGraph_PoC.clients import WorkflowClients
from langGraph_PoC.config import load_settings
from langGraph_PoC.conversation_memory import ConversationMemory, ConversationStorageError
from langGraph_PoC.models import NonBlankString
from langGraph_PoC.workflow import build_graph
from langGraph_PoC.workflow_persistence import WorkflowPersistenceError, open_checkpointer

from api_models import ResumeWorkflow, StartWorkflow, WorkflowResponse
from api_service import ApiError, WorkflowService


API_DB_PATH = Path(__file__).resolve().parent / "workflow_state.sqlite"


def create_app(*, clients_factory=WorkflowClients, memory_factory=ConversationMemory.connect,
               settings_factory=load_settings, checkpoint_path: Path = API_DB_PATH):
    """Inject fake clients/storage in tests without modifying the original project."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            async with AsyncExitStack() as stack:
                settings = settings_factory()
                checkpointer = await stack.enter_async_context(open_checkpointer(checkpoint_path))
                clients = clients_factory(settings)
                stack.push_async_callback(clients.aclose)
                probe = await memory_factory(None)
                try:
                    await probe.load()
                finally:
                    await probe.close()
                application.state.workflow_service = WorkflowService(
                    build_graph(clients, checkpointer=checkpointer), memory_factory,
                )
                yield
        except (ValueError, ConversationStorageError, WorkflowPersistenceError, sqlite3.Error, PyMongoError):
            raise RuntimeError(
                "Workflow API initialization/storage failed. Check the shared configuration, "
                "MongoDB connectivity, and API SQLite database."
            ) from None

    application = FastAPI(
        title="LangGraph Leave Workflow API", version="1.0.0", lifespan=lifespan,
        description="Start a leave workflow, answer its interrupt prompts, and recover saved API workflows.",
    )

    @application.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError):
        content = {"detail": error.detail}
        if error.thread_id is not None:
            content["thread_id"] = error.thread_id
        if error.conversation_id is not None:
            content["conversation_id"] = error.conversation_id
        return JSONResponse(status_code=error.status_code, content=content)

    responses = {404: {"description": "Workflow not found"}, 409: {"description": "Conflicting action"},
                 503: {"description": "Workflow storage unavailable"}}

    @application.post("/workflows", response_model=WorkflowResponse, status_code=201,
                      responses={409: responses[409], 503: responses[503]})
    async def start_workflow(body: StartWorkflow, request: Request):
        return await request.app.state.workflow_service.start(body)

    @application.get("/workflows/{thread_id}", response_model=WorkflowResponse, responses=responses)
    async def get_workflow(thread_id: NonBlankString, request: Request):
        return await request.app.state.workflow_service.get(thread_id)

    @application.post("/workflows/{thread_id}/resume", response_model=WorkflowResponse, responses=responses)
    async def resume_workflow(thread_id: NonBlankString, body: ResumeWorkflow, request: Request):
        return await request.app.state.workflow_service.resume(thread_id, body)

    @application.post("/workflows/{thread_id}/cancel", response_model=WorkflowResponse, responses=responses)
    async def cancel_workflow_endpoint(thread_id: NonBlankString, request: Request):
        return await request.app.state.workflow_service.cancel(thread_id)

    return application


app = create_app()
