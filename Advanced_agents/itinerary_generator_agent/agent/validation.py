"""Deterministic checks are separate from the evaluator's subjective judgments."""

from agent.models import Catalog, ItineraryPlan, StepResult, TripRequest
from agent.state import plan_steps


def completed_changes(plan: ItineraryPlan, completed: list[dict]) -> list[str]:
    steps = {step.key: step for step in plan_steps(plan)}
    changes = []
    for value in completed:
        step = StepResult.model_validate(value).step
        if step.key not in steps or steps[step.key].day != step.day:
            changes.append(f"Completed {step.key} must remain on its original day.")
    return changes


def inspect_plan(plan: ItineraryPlan, request: TripRequest, catalog: Catalog, completed: list[dict]) -> dict[str, list[str]]:
    issues = {"grounding": [], "availability": [], "pacing": []}
    activity_ids = [item_id for day in plan.days for item_id in day.activity_ids]
    if sorted(day.day for day in plan.days) != list(range(1, request.nights + 2)):
        issues["pacing"].append("Include exactly one entry for every day from 1 through nights + 1.")
    if any(len(day.activity_ids) > 2 for day in plan.days):
        issues["pacing"].append("Schedule at most two activities per day.")
    if len(activity_ids) != len(set(activity_ids)):
        issues["grounding"].append("An activity can be selected only once.")
    issues["grounding"].extend(completed_changes(plan, completed))
    last_day = max((value["step"]["day"] or 1 for value in completed), default=1)
    done = {StepResult.model_validate(value).step.key for value in completed}
    for step in plan_steps(plan):
        items = catalog.hotels if step.kind == "hotel" else catalog.activities
        item = items.get(step.item_id)
        if item is None or item.destination_id != request.destination_id:
            issues["grounding"].append(f"{step.key} is not a catalog item for this destination.")
        elif not item.available:
            issues["availability"].append(f"{step.key} is unavailable.")
        if step.kind == "activity" and step.key not in done and step.day < last_day:
            issues["pacing"].append("New pending activities cannot precede the last completed activity's day.")
    return issues
