from agent.errors import ServiceError
from agent.models import ItineraryPlan, PlanDecision, TripRequest
from agent.nodes.context import PLAN_RULES, planning_context
from agent.state import AgentState, trace
from agent.validation import completed_changes


def replanner(state: AgentState, services) -> dict:
    if state["replan_count"] >= services.max_replans:
        message = f"Stopped after {services.max_replans} revisions. Remaining constraints could not be satisfied."
        return {"status": "incomplete", "error": message, "events": trace(state, "replanner", message)}
    count = state["replan_count"] + 1
    try:
        request = TripRequest.model_validate(state["request"])
        catalog = services.tools.catalog(request.destination_id)
        context = planning_context({**state, "catalog": catalog.model_dump()})
        decision = services.llm.structured(
            PlanDecision, "You are the Replanner. Resolve the observed failure or reflection feedback.\n" + PLAN_RULES, context,
        )
        if decision.itinerary is None:
            return {"status": "incomplete", "error": decision.reason, "replan_count": count,
                    "catalog": catalog.model_dump(), "events": trace(state, "replanner", decision.reason)}
        proposal = ItineraryPlan.model_validate(decision.itinerary)
        changes = completed_changes(proposal, state["completed"])
        if changes:
            # Keep the previous plan instead of accepting a rewritten completed step.
            return {"replan_count": count, "catalog": catalog.model_dump(), "feedback": changes,
                    "events": trace(state, "replanner", "Rejected revision that changed completed work: " + " ".join(changes))}
        return {"plan": proposal.model_dump(), "catalog": catalog.model_dump(), "replan_count": count,
                "events": trace(state, "replanner", decision.reason)}
    except ServiceError as error:
        return {"status": "error", "error": str(error), "replan_count": count,
                "events": trace(state, "replanner", str(error))}
