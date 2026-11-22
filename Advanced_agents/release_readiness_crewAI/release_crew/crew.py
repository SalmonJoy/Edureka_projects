"""The worker list excludes the manager, as required by CrewAI hierarchy."""

from crewai import Crew, Process
from crewai.utilities.task_output_storage_handler import TaskOutputStorageHandler
from pydantic import PrivateAttr

from release_crew.agents import create_agents
from release_crew.config import Settings
from release_crew.evidence import EvidenceSession
from release_crew.tasks import create_tasks


class SessionTaskOutputs(TaskOutputStorageHandler):
    """Keep CrewAI's internal task log in this session instead of shared SQLite."""

    def __init__(self):
        self.outputs = {}

    def reset(self):
        self.outputs.clear()

    def update(self, task_index, log):
        self.outputs[task_index] = log

    def load(self):
        return list(self.outputs.values())


class SessionCrew(Crew):
    # One small SDK adapter prevents reports from being implicitly saved to disk.
    _task_output_handler: TaskOutputStorageHandler = PrivateAttr(default_factory=SessionTaskOutputs)


def build_crew(session: EvidenceSession, settings: Settings, llm=None) -> Crew:
    manager, workers = create_agents(session, settings, llm=llm)
    return SessionCrew(
        agents=list(workers.values()), manager_agent=manager,
        tasks=create_tasks(session, workers), process=Process.hierarchical,
        memory=False, planning=False, cache=False, verbose=False,
        tracing=False, share_crew=False,
    )
