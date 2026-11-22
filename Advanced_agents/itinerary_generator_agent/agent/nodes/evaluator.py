from agent.errors import ServiceError, TravelToolError
from agent.models import CriterionScore, EvaluationResult, ItineraryPlan, SoftReview, TripRequest
from agent.nodes.context import planning_context
from agent.state import AgentState, remaining_steps, trace
from agent.validation import completed_changes, inspect_plan


def reflect(state: AgentState, services) -> dict:
    request = TripRequest.model_validate(state["request"])
    plan = ItineraryPlan.model_validate(state["plan"])
    try:
        catalog = services.tools.catalog(request.destination_id)
        # A completed check is historical, not a booking. Do not quietly replace it.
        for result in state["completed"]:
            step = result["step"]
            items = catalog.hotels if step["kind"] == "hotel" else catalog.activities
            item = items.get(step["item_id"])
            if item is None or not item.available:
                message = "A completed simulated choice is no longer available. Start a new run to replace completed work."
                criteria = dict(state["reflection"]["criteria"])
                criteria["availability"] = {"score": 0, "reason": message}
                criteria["budget"] = {"score": 0, "reason": "A final budget cannot be validated for the unavailable completed choice."}
                if item is None:
                    criteria["grounding"] = {"score": 0, "reason": "A completed item is no longer present in the catalog."}
                failed = EvaluationResult(
                    criteria=criteria, total=sum(value["score"] for value in criteria.values()),
                    passed=False, issues=[message],
                ).model_dump()
                return {"status": "incomplete", "error": message, "budget": None,
                        "reflection": failed, "evaluations": [*state["evaluations"], failed],
                        "catalog": catalog.model_dump(), "events": trace(state, "reflection", message)}
        if completed_changes(plan, state["completed"]):
            raise ServiceError("The current plan unexpectedly changed completed work.")

        issues = inspect_plan(plan, request, catalog, state["completed"])
        quote = None
        budget_issues = []
        if not issues["grounding"] and not issues["availability"]:
            try:
                quote = services.tools.quote(request, plan)
                if quote["total"] > request.max_budget:
                    budget_issues.append(f"API total INR {quote['total']} exceeds the INR {request.max_budget} budget.")
            except TravelToolError as error:
                key = "availability" if error.status_code == 409 else "grounding"
                issues[key].append(str(error))
        else:
            budget_issues.append("A current budget cannot be quoted until catalog/availability issues are resolved.")

        criteria = {
            "budget": CriterionScore(score=2 if quote is not None and not budget_issues else 0, reason=" ".join(budget_issues) or (f"API quote: INR {quote['total']}, within budget." if quote else "No valid budget quote.")),
            "availability": CriterionScore(score=0 if issues["availability"] else 2, reason=" ".join(issues["availability"]) or "All selected catalog items are currently available."),
            "grounding": CriterionScore(score=0 if issues["grounding"] else 2, reason=" ".join(issues["grounding"]) or "Selections are unique catalog IDs for this destination."),
            "pacing": CriterionScore(score=0 if issues["pacing"] else 2, reason=" ".join(issues["pacing"]) or "Every trip day is covered, with at most two activities per day."),
            "preferences": CriterionScore(score=0, reason="Not assessed until deterministic checks pass."),
        }
        feedback = [*budget_issues, *issues["grounding"], *issues["availability"], *issues["pacing"]]
        hard_pass = quote is not None and not feedback
        if hard_pass:
            review = services.llm.structured(
                SoftReview,
                "You are the Reflection Evaluator. Score preference fit and day pacing from 0 to 2 "
                "(0=poor, 1=acceptable, 2=good). Assess only the supplied itinerary/catalog. "
                "Unpriced rest/free time is valid; do not require invented activities or distances. "
                "When the user has no explicit or relevant recalled preferences, preference fit is 2. "
                "Current explicit preferences override memory. Return concrete improvements for low scores. "
                "Do not claim reservations or change Python's budget/availability/grounding checks.",
                {**planning_context({**state, "catalog": catalog.model_dump()}), "api_budget": quote},
            )
            criteria["preferences"] = review.preferences
            criteria["pacing"] = review.pacing
            if review.preferences.score == 0 or review.pacing.score == 0:
                feedback = review.improvements or [review.preferences.reason, review.pacing.reason]

        total = sum(score.score for score in criteria.values())
        passed = hard_pass and total >= 8 and all(score.score >= 1 for score in criteria.values())
        result = EvaluationResult(criteria=criteria, total=total, passed=passed, issues=[] if passed else feedback)
        pending = remaining_steps(plan, state["completed"])
        status = "complete" if passed and not pending else "running"
        label = "passed" if passed else "needs revision"
        return {
            "catalog": catalog.model_dump(), "budget": quote, "reflection": result.model_dump(),
            "evaluations": [*state["evaluations"], result.model_dump()],
            "feedback": result.issues, "pending": pending, "status": status,
            "events": trace(state, "reflection", f"Rubric {total}/10: {label}. {len(pending)} pending check(s)."),
        }
    except ServiceError as error:
        return {"status": "error", "error": str(error), "budget": None,
                "events": trace(state, "reflection", str(error))}
