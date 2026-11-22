from copy import deepcopy

import pytest

from agent.errors import ServiceError, TravelToolError
from agent.models import TripRequest
from conftest import finish, sample_plan


def test_pause_then_complete_without_duplicate_execution(runtime, services):
    view = runtime.start_trip(TripRequest())
    assert view["status"] == "paused"
    assert view["reflection"]["passed"] is True
    assert view["pending"][0]["kind"] == "hotel"
    assert services.tools.checks == []
    assert services.memory.save_calls == 0
    view = finish(runtime, view)
    assert view["status"] == "complete"
    assert view["budget"]["total"] == 7800
    assert len(view["completed"]) == 3
    assert services.tools.checks == ["hotel:jaipur-haveli", "activity:jaipur-food", "activity:jaipur-fort"]
    assert view["memory_saved"] is True
    runtime.continue_trip(view["run_id"])
    assert len(services.tools.checks) == 3
    assert services.memory.save_calls == 1


def test_hotel_unavailable_replans_before_any_completion(runtime, services):
    view = runtime.start_trip(TripRequest())
    services.tools.data.hotels["jaipur-haveli"].available = False
    view = runtime.continue_trip(view["run_id"])
    assert view["status"] == "paused"
    assert view["replan_count"] == 1
    assert view["completed"] == []
    assert view["initial_plan"]["hotel_id"] == "jaipur-haveli"
    assert view["plan"]["hotel_id"] == "jaipur-market"
    assert view["observations"][-1]["outcome"] == "unexpected"
    assert finish(runtime, view)["status"] == "complete"


def test_activity_failure_preserves_completed_work(runtime, services):
    view = runtime.start_trip(TripRequest())
    view = runtime.continue_trip(view["run_id"])
    view = runtime.continue_trip(view["run_id"])
    completed = deepcopy(view["completed"])
    services.tools.data.activities["jaipur-fort"].available = False
    view = runtime.continue_trip(view["run_id"])
    assert view["status"] == "complete"
    assert view["completed"] == completed
    assert view["replan_count"] == 1
    assert "jaipur-fort" not in [item for day in view["plan"]["days"] for item in day["activity_ids"]]
    assert services.tools.checks.count("activity:jaipur-food") == 1


def test_price_change_triggers_fresh_quote(runtime, services):
    view = runtime.start_trip(TripRequest())
    services.tools.data.hotels["jaipur-haveli"].price_per_night = 2600
    view = runtime.continue_trip(view["run_id"])
    assert view["replan_count"] == 1
    assert "price changed" in view["observations"][0]["observation"]
    assert view["budget"]["hotel_total"] == 5200
    assert finish(runtime, view)["status"] == "complete"


def test_reflection_feedback_causes_revision(runtime, services):
    services.llm.reviews = [{"preferences": {"score": 0, "reason": "Poor fit"}, "pacing": {"score": 2, "reason": "Fine"}, "improvements": ["Improve preference fit."]}]
    view = runtime.start_trip(TripRequest())
    assert view["status"] == "paused"
    assert view["replan_count"] == 1
    replan_call = next(call for call in services.llm.calls if "Replanner" in call["instructions"])
    assert "Improve preference fit." in replan_call["context"]["feedback"]
    assert len(view["evaluations"]) == 2


def test_shared_revision_limit_on_budget_failure(runtime, services):
    view = runtime.start_trip(TripRequest(max_budget=1))
    assert view["status"] == "incomplete"
    assert view["replan_count"] == 3
    assert view["reflection"]["passed"] is False
    assert services.tools.checks == []
    assert view["memory_saved"] is True


@pytest.mark.parametrize("change", [
    lambda plan: plan.update(hotel_id="imaginary"),
    lambda plan: plan["days"][0]["activity_ids"].append("jaipur-food"),
    lambda plan: plan["days"].pop(),
    lambda plan: plan["days"][0]["activity_ids"].extend(["jaipur-fort", "jaipur-craft"]),
])
def test_invalid_semantic_plans_are_revised_before_execution(runtime, services, change):
    plan = sample_plan()
    change(plan)
    services.llm.decisions = [{"itinerary": plan, "reason": "Initial proposal"}]
    view = runtime.start_trip(TripRequest())
    assert view["status"] == "paused"
    assert view["replan_count"] == 1
    assert services.tools.checks == []


def test_replanner_cannot_replace_completed_hotel(runtime, services):
    view = runtime.start_trip(TripRequest())
    view = runtime.continue_trip(view["run_id"])
    completed = deepcopy(view["completed"])
    services.tools.data.activities["jaipur-food"].available = False
    bad = sample_plan()
    bad["hotel_id"] = "jaipur-market"
    services.llm.decisions = [{"itinerary": bad, "reason": "Attempted hotel replacement"}]
    view = runtime.continue_trip(view["run_id"])
    assert view["plan"]["hotel_id"] == "jaipur-haveli"
    assert view["completed"] == completed
    assert any("Rejected revision" in event["message"] for event in view["events"])


def test_invalidated_completed_choice_stops_incomplete(runtime, services):
    view = runtime.start_trip(TripRequest())
    view = runtime.continue_trip(view["run_id"])
    services.tools.data.hotels["jaipur-haveli"].available = False
    view = finish(runtime, view)
    assert view["status"] == "incomplete"
    assert "completed simulated choice" in view["error"]
    assert view["plan"]["hotel_id"] == "jaipur-haveli"
    assert view["budget"] is None
    assert view["reflection"]["passed"] is False
    assert view["reflection"]["criteria"]["availability"]["score"] == 0


def test_no_feasible_alternative(runtime, services):
    view = runtime.start_trip(TripRequest())
    for hotel in services.tools.data.hotels.values():
        hotel.available = False
    view = runtime.continue_trip(view["run_id"])
    assert view["status"] == "incomplete"
    assert "No available hotel" in view["error"]
    assert view["completed"] == []


def test_memories_are_supplied_alongside_current_preferences(runtime, services):
    services.memory.matches = [{"id": "old", "type": "preference", "text": "Busy adventure trips", "score": 0.8, "created_at": "2026-01-01"}]
    view = runtime.start_trip(TripRequest(preferences="Quiet cultural activities"))
    context = services.llm.calls[0]["context"]
    assert context["request"]["preferences"] == "Quiet cultural activities"
    assert context["memories"][0]["text"] == "Busy adventure trips"
    assert view["memories"] == services.memory.matches
    assert "override" in services.llm.calls[0]["instructions"]


@pytest.mark.parametrize("failure", ["catalog", "memory", "model", "budget"])
def test_service_failures_do_not_create_fake_success(runtime, services, failure):
    if failure == "catalog":
        services.tools.fail = True
    elif failure == "memory":
        services.memory.fail_read = True
    elif failure == "model":
        services.llm.fail = True
    else:
        services.tools.quote_failure = ServiceError("Budget service offline.")
    view = runtime.start_trip(TripRequest())
    assert view["status"] == "error"
    assert view["completed"] == []
    assert view["error"]


def test_step_service_outage_stops_without_completion(runtime, services):
    view = runtime.start_trip(TripRequest())
    services.tools.fail = True
    view = runtime.continue_trip(view["run_id"])
    assert view["status"] == "error"
    assert view["completed"] == []
    assert view["observations"][-1]["outcome"] == "error"


def test_budget_rejection_is_replanned_not_a_success(runtime, services):
    services.tools.quote_failure = TravelToolError("Availability changed", 409)
    view = runtime.start_trip(TripRequest())
    assert view["status"] == "incomplete"
    assert view["replan_count"] == 3
    assert services.tools.checks == []


def test_memory_write_failure_retains_valid_itinerary(runtime, services):
    services.memory.fail_save = True
    view = finish(runtime, runtime.start_trip(TripRequest()))
    assert view["status"] == "complete"
    assert view["memory_saved"] is False
    assert "not confirmed" in view["memory_error"]


def test_separate_runs_have_separate_checkpoints(runtime):
    first = runtime.start_trip(TripRequest(user_id="first"))
    second = runtime.start_trip(TripRequest(user_id="second"))
    runtime.continue_trip(first["run_id"])
    assert runtime.view(second["run_id"])["completed"] == []
    with pytest.raises(ValueError, match="does not exist"):
        runtime.continue_trip("unknown")
