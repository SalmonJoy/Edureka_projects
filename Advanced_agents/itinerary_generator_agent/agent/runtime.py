"""Shared start/continue interface for Streamlit, the CLI, and tests."""

from uuid import uuid4

from langgraph.types import Command

from agent.config import Settings, load_settings
from agent.graph import build_graph
from agent.llm import StructuredLLM
from agent.memory import QdrantMemory
from agent.models import TripRequest
from agent.services import Services


class AgentRuntime:
    def __init__(self, services: Services):
        self.services = services
        self.graph = build_graph(services)

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 200}

    def view(self, run_id: str) -> dict:
        snapshot = self.graph.get_state(self._config(run_id))
        if not snapshot.values:
            raise ValueError("This run does not exist in the current session. Start a new trip.")
        state = dict(snapshot.values)
        if any(task.interrupts for task in snapshot.tasks):
            state["status"] = "paused"
        return state

    def start_trip(self, request: TripRequest, *, is_test: bool = False) -> dict:
        run_id = str(uuid4())
        state = {
            "run_id": run_id, "request": request.model_dump(), "is_test": is_test,
            "catalog": {}, "memories": [], "memory_ready": False,
            "memory_saved": False, "memory_error": None,
            "initial_plan": None, "plan": None, "pending": [], "completed": [],
            "observations": [], "budget": None, "reflection": None, "evaluations": [],
            "feedback": [], "events": [], "replan_count": 0, "status": "running", "error": None,
        }
        self.graph.invoke(state, config=self._config(run_id))
        return self.view(run_id)

    def continue_trip(self, run_id: str) -> dict:
        state = self.view(run_id)
        if state["status"] == "paused":
            self.graph.invoke(Command(resume=True), config=self._config(run_id))
        # Continuing a finished run is a read: no duplicate checks or writes.
        return self.view(run_id)

    def close(self) -> None:
        self.services.close()


def create_runtime(settings: Settings | None = None) -> AgentRuntime:
    from agent.tools import TravelTools

    settings = settings if settings is not None else load_settings()
    return AgentRuntime(Services(
        tools=TravelTools(settings.travel_url), llm=StructuredLLM(settings),
        memory=QdrantMemory(settings), max_replans=settings.max_replans,
    ))
