from agent.models import ItineraryPlan, Step, StepResult, TripRequest
from agent.validation import completed_changes, inspect_plan
from conftest import sample_catalog, sample_plan


def completed_activity():
    return StepResult(step=Step(kind="activity", item_id="jaipur-fort", day=2), outcome="success", observation="Checked").model_dump()


def test_completed_day_cannot_move():
    plan = sample_plan()
    plan["days"][1]["activity_ids"] = []
    plan["days"][2]["activity_ids"] = ["jaipur-fort"]
    assert completed_changes(ItineraryPlan.model_validate(plan), [completed_activity()])


def test_new_activity_cannot_be_inserted_before_completed_day():
    plan = ItineraryPlan.model_validate(sample_plan())
    issues = inspect_plan(plan, TripRequest(), sample_catalog(), [completed_activity()])
    assert any("precede" in message for message in issues["pacing"])


def test_free_time_days_are_valid():
    plan = sample_plan()
    for day in plan["days"]:
        day["activity_ids"] = []
    assert not any(inspect_plan(ItineraryPlan.model_validate(plan), TripRequest(), sample_catalog(), []).values())
