"""Only JSON-friendly values cross node boundaries and enter checkpoints."""

from typing import TypedDict

from agent.models import ItineraryPlan, Step, StepResult


class AgentState(TypedDict, total=False):
    run_id: str
    request: dict
    is_test: bool
    catalog: dict
    memories: list[dict]
    memory_ready: bool
    memory_saved: bool
    memory_error: str | None
    initial_plan: dict | None
    plan: dict | None
    pending: list[dict]
    completed: list[dict]
    observations: list[dict]
    budget: dict | None
    reflection: dict | None
    evaluations: list[dict]
    feedback: list[str]
    events: list[dict]
    replan_count: int
    status: str
    error: str | None


def trace(state: AgentState, node: str, message: str) -> list[dict]:
    return [*state.get("events", []), {"node": node, "message": message}]


def plan_steps(plan: ItineraryPlan) -> list[Step]:
    return [Step(kind="hotel", item_id=plan.hotel_id)] + [
        Step(kind="activity", item_id=item_id, day=day.day)
        for day in sorted(plan.days, key=lambda value: value.day)
        for item_id in day.activity_ids
    ]


def remaining_steps(plan: ItineraryPlan, completed: list[dict]) -> list[dict]:
    done = {StepResult.model_validate(result).step.key for result in completed}
    return [step.model_dump() for step in plan_steps(plan) if step.key not in done]
