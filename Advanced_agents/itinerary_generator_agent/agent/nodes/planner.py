from agent.errors import ServiceError
from agent.models import PlanDecision
from agent.nodes.context import PLAN_RULES, planning_context
from agent.state import AgentState, trace


def planner(state: AgentState, services) -> dict:
    try:
        decision = services.llm.structured(PlanDecision, "You are the initial itinerary Planner.\n" + PLAN_RULES, planning_context(state))
        if decision.itinerary is None:
            return {"status": "incomplete", "error": decision.reason, "events": trace(state, "planner", decision.reason)}
        plan = decision.itinerary.model_dump()
        return {"initial_plan": plan, "plan": plan, "events": trace(state, "planner", decision.reason)}
    except ServiceError as error:
        return {"status": "error", "error": str(error), "events": trace(state, "planner", str(error))}
