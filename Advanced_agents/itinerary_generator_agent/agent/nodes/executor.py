from langgraph.types import interrupt

from agent.errors import ServiceError
from agent.models import Step, StepResult, TripRequest
from agent.state import AgentState, trace


def pause_before_step(state: AgentState) -> dict:
    # Keep this node free of API calls and writes: interrupt resumes by rerunning it.
    interrupt({"message": "Change availability in itinerary_guide if desired, then continue.", "next_step": state["pending"][0]})
    return {"events": trace(state, "pause", "Continued to the next simulated check.")}


def execute_step(state: AgentState, services) -> dict:
    step = Step.model_validate(state["pending"][0])
    request = TripRequest.model_validate(state["request"])
    try:
        item = services.tools.check_step(request, step)
        old_items = state["catalog"]["hotels" if step.kind == "hotel" else "activities"]
        old = old_items.get(step.item_id, {})
        price_key = "price_per_night" if step.kind == "hotel" else "price_per_person"
        if item is None:
            outcome, observation = "unexpected", f"{step.key} is no longer present in the catalog."
        elif not item.available:
            outcome, observation = "unexpected", f"{item.name} became unavailable before its simulated check."
        elif old.get(price_key) != getattr(item, price_key):
            outcome, observation = "unexpected", f"{item.name}'s price changed; revise and obtain a fresh budget quote."
        else:
            outcome, observation = "success", f"Checked {item.name}: available. This is a simulation, not a booking."
        result = StepResult(step=step, outcome=outcome, observation=observation, snapshot=item.model_dump() if item else {}).model_dump()
        updates = {"observations": [*state["observations"], result], "events": trace(state, "executor", observation)}
        if outcome == "success":
            updates.update(completed=[*state["completed"], result], pending=state["pending"][1:], feedback=[])
        else:
            updates["feedback"] = [observation]
        return updates
    except ServiceError as error:
        result = StepResult(step=step, outcome="error", observation=str(error)).model_dump()
        return {"status": "error", "error": str(error), "observations": [*state["observations"], result],
                "events": trace(state, "executor", str(error))}
