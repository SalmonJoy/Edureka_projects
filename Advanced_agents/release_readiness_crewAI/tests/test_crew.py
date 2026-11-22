import json
from types import SimpleNamespace

from crewai import Process
from crewai.llms.base_llm import BaseLLM
from pydantic import PrivateAttr

from release_crew.crew import SessionTaskOutputs, build_crew
from release_crew.evidence import ROLES
from release_crew.models import ReviewRequest
from release_crew.runner import run_review
from release_crew.validation import parse_output
from conftest import assessment_for, manager_for


class ScriptedLLM(BaseLLM):
    """Exercise the real CrewAI hierarchy without calling any provider."""
    _session = PrivateAttr()

    def __init__(self, session):
        super().__init__(model="offline-scripted")
        self._session = session

    def supports_function_calling(self):
        return False

    def supports_stop_words(self):
        return False

    def get_input_tokens_count(self, messages, tools=None, response_model=None):
        return 100

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None, **kwargs):
        role = getattr(from_agent, "role", "")
        session = self._session
        if role in ROLES.values():
            domain = next(key for key, name in ROLES.items() if name == role)
            if domain not in session.served_domains:
                return f'Thought: Read the assigned evidence.\nAction: read_{domain}_evidence\nAction Input: {{}}'
            return "Thought: Report the observed evidence.\nFinal Answer: " + assessment_for(session, domain).model_dump_json()
        name = getattr(from_task, "name", "")
        if name == "release_readiness_report":
            return "Thought: Consolidate the three reports.\nFinal Answer: " + manager_for(session).model_dump_json()
        domain = name.removesuffix("_review")
        if domain in ROLES and domain not in session.served_domains:
            args = {"coworker": ROLES[domain], "task": f"Review {domain} evidence and return the structured assessment.", "context": json.dumps(session.request.model_dump())}
            return "Thought: Delegate to the assigned specialist.\nAction: Delegate work to coworker\nAction Input: " + json.dumps(args)
        if domain in ROLES:
            return "Thought: Return the specialist's assessment.\nFinal Answer: " + assessment_for(session, domain).model_dump_json()
        raise AssertionError(f"Unexpected scripted call: role={role}, task={name}")


def test_crew_configuration_and_tool_boundaries(session, settings):
    crew = build_crew(session, settings, llm=ScriptedLLM(session))
    assert crew.process == Process.hierarchical
    assert crew.manager_agent is not None
    assert crew.manager_agent.role == "Release manager"
    assert crew.manager_agent.allow_delegation is True
    assert len(crew.agents) == 3 and crew.manager_agent not in crew.agents
    assert {agent.role for agent in crew.agents} == set(ROLES.values())
    assert all(not agent.allow_delegation and len(agent.tools or []) == 1 for agent in crew.agents)
    assert all(tool.args_schema is not None and tool.args_schema.model_json_schema().get("additionalProperties") is False for agent in crew.agents for tool in (agent.tools or []))
    assert len(crew.tasks) == 4
    assert crew.tasks[-1].context == crew.tasks[:3]
    assert all(task.guardrail_max_retries == 1 for task in crew.tasks)
    assert not crew.memory and not crew.planning and not crew.cache
    assert isinstance(crew._task_output_handler, SessionTaskOutputs)


def test_real_crewai_hierarchy_performs_all_three_delegations(client, settings):
    def factory(session, settings):
        return build_crew(session, settings, llm=ScriptedLLM(session))
    result = run_review(ReviewRequest(release_id="rel-110"), settings, client=client, crew_factory=factory)
    assert result.status == "complete", result.error or [event.model_dump() for event in result.activity]
    assert result.recommendation == "NO_GO"
    assert {item.domain for item in result.assessments} == set(ROLES)
    assert len([event for event in result.activity if event.operation == "delegated_review"]) == 3


def test_invalid_json_fails_instead_of_becoming_a_report():
    import pytest
    from pydantic import ValidationError
    from release_crew.models import ManagerReport
    with pytest.raises(ValidationError):
        parse_output(SimpleNamespace(pydantic=None, raw="not JSON"), ManagerReport)


def test_internal_logs_are_in_memory_and_independent():
    first, second = SessionTaskOutputs(), SessionTaskOutputs()
    first.update(0, {"output": "First report"})
    assert second.load() == []
    first.reset()
    assert first.load() == []
