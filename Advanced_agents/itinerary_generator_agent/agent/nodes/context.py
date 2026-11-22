from agent.errors import ServiceError
from agent.models import TripRequest
from agent.state import AgentState, trace


def load_context(state: AgentState, services) -> dict:
    request = TripRequest.model_validate(state["request"])
    try:
        catalog = services.tools.catalog(request.destination_id)
        return {"catalog": catalog.model_dump(), "events": trace(state, "catalog", "Loaded current hotels and activities from the travel API.")}
    except ServiceError as error:
        return {"status": "error", "error": str(error), "events": trace(state, "catalog", str(error))}


def planning_context(state: AgentState) -> dict:
    return {
        "request": state["request"], "catalog": state["catalog"],
        "memories": state["memories"], "current_plan": state.get("plan"),
        "completed_steps": state["completed"], "observations": state["observations"],
        "feedback": state["feedback"],
    }


PLAN_RULES = """Create a feasible structured itinerary using only the supplied catalog IDs.
Use one available hotel for all nights. Include exactly nights + 1 numbered days, starting at 1.
Select each activity at most once and at most two activities per day. Empty activity lists
with unpriced free_time/rest are allowed. Do not invent paid activities, prices, dates,
bookings, or travel distances. Prefer activities relevant to the user's preferences.
Food, transport, and miscellaneous amounts are whole-trip group totals. Hotel cost is
nightly price * nights * rooms; activities cost per-person price * travelers.
Stay within max_budget. Python and the travel API will validate all constraints.
Current explicit preferences override historical memories. If explicit preferences are blank,
use relevant recent stored preferences; otherwise no preference requirement is imposed.
Return itinerary=null with a clear reason if no feasible plan exists.
Keep all completed hotel/activity selections, and completed activities' day numbers unchanged.
Never add an activity before the last completed activity's day. Revise only pending work.
"""
