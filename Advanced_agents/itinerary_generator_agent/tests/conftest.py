"""Small test doubles keep graph tests deterministic and free of API charges."""

from copy import deepcopy

import pytest

from agent.errors import ServiceError, TravelToolError
from agent.config import Settings
from agent.models import Catalog, PlanDecision, SoftReview
from agent.runtime import AgentRuntime
from agent.services import Services


def sample_catalog() -> Catalog:
    return Catalog.model_validate({
        "destination": {"id": "jaipur", "name": "Jaipur", "description": "Historic forts and local food."},
        "hotels": {
            "jaipur-haveli": {"id": "jaipur-haveli", "destination_id": "jaipur", "name": "Pink City Haveli", "description": "Heritage stay", "price_per_night": 2500, "available": True},
            "jaipur-market": {"id": "jaipur-market", "destination_id": "jaipur", "name": "Market View Lodge", "description": "Budget stay", "price_per_night": 1500, "available": True},
        },
        "activities": {
            "jaipur-food": {"id": "jaipur-food", "destination_id": "jaipur", "name": "Local Food Walk", "description": "Food", "price_per_person": 600, "available": True},
            "jaipur-fort": {"id": "jaipur-fort", "destination_id": "jaipur", "name": "Fort Tour", "description": "Culture", "price_per_person": 800, "available": True},
            "jaipur-craft": {"id": "jaipur-craft", "destination_id": "jaipur", "name": "Block Printing Workshop", "description": "Craft", "price_per_person": 1000, "available": True},
        },
    })


def sample_plan() -> dict:
    return {
        "hotel_id": "jaipur-haveli", "rationale": "Culture and local food with time to rest.",
        "days": [
            {"day": 1, "activity_ids": ["jaipur-food"], "free_time": "Rest"},
            {"day": 2, "activity_ids": ["jaipur-fort"], "free_time": "Leisure"},
            {"day": 3, "activity_ids": [], "free_time": "Rest before departure"},
        ],
    }


class FakeTools:
    def __init__(self):
        self.data = sample_catalog()
        self.checks = []
        self.fail = False
        self.quote_failure = None

    def close(self):
        pass

    def destinations(self):
        if self.fail:
            raise ServiceError("Travel service offline.")
        return [self.data.destination]

    def catalog(self, destination_id):
        if self.fail:
            raise ServiceError("Travel service offline.")
        return self.data.model_copy(deep=True)

    def check_step(self, request, step):
        if self.fail:
            raise ServiceError("Travel service offline.")
        self.checks.append(step.key)
        items = self.data.hotels if step.kind == "hotel" else self.data.activities
        return items.get(step.item_id)

    def quote(self, request, plan):
        if self.quote_failure:
            raise self.quote_failure
        ids = [item_id for day in plan.days for item_id in day.activity_ids]
        hotel = self.data.hotels[plan.hotel_id]
        activities = [self.data.activities[item_id] for item_id in ids]
        if not all(item.available for item in [hotel, *activities]):
            raise TravelToolError("An item became unavailable during quoting.", 409)
        hotel_line = {"id": hotel.id, "name": hotel.name, "unit_price": hotel.price_per_night,
                      "quantity": request.nights * request.rooms, "total": hotel.price_per_night * request.nights * request.rooms}
        lines = [{"id": item.id, "name": item.name, "unit_price": item.price_per_person,
                  "quantity": request.travelers, "total": item.price_per_person * request.travelers} for item in activities]
        activities_total = sum(line["total"] for line in lines)
        total = hotel_line["total"] + activities_total + request.food_total + request.transport_total + request.misc_total
        return {"destination": "Jaipur", "currency": "INR", "hotel": hotel_line, "activities": lines,
                "hotel_total": hotel_line["total"], "activities_total": activities_total,
                "food_total": request.food_total, "transport_total": request.transport_total,
                "misc_total": request.misc_total, "total": total, "per_traveler": round(total / request.travelers, 2)}


class FakeLLM:
    def __init__(self):
        self.decisions = []
        self.reviews = []
        self.calls = []
        self.fail = False

    def structured(self, schema, instructions, context):
        self.calls.append({"schema": schema.__name__, "instructions": instructions, "context": deepcopy(context)})
        if self.fail:
            raise ServiceError("Model service offline.")
        if schema is SoftReview:
            return SoftReview.model_validate(self.reviews.pop(0) if self.reviews else {
                "preferences": {"score": 2, "reason": "Matches the stated preferences."},
                "pacing": {"score": 2, "reason": "Relaxed pace."}, "improvements": [],
            })
        assert schema is PlanDecision
        if self.decisions:
            return PlanDecision.model_validate(self.decisions.pop(0))
        plan = sample_plan()
        catalog = context["catalog"]
        hotels = [item for item in catalog["hotels"].values() if item["available"]]
        if not hotels:
            return PlanDecision(itinerary=None, reason="No available hotel remains.")
        plan["hotel_id"] = hotels[0]["id"]
        for day in plan["days"]:
            day["activity_ids"] = [item_id for item_id in day["activity_ids"] if catalog["activities"][item_id]["available"]]
        return PlanDecision(itinerary=plan, reason="Selected feasible catalog options.")


class FakeMemory:
    def __init__(self):
        self.matches = []
        self.saved = {}
        self.save_calls = 0
        self.fail_read = False
        self.fail_save = False

    def close(self):
        pass

    def retrieve(self, request, *, is_test=False):
        if self.fail_read:
            raise ServiceError("Memory retrieval failed.")
        return deepcopy(self.matches)

    def save(self, state):
        self.save_calls += 1
        if self.fail_save:
            raise ServiceError("Memory write not confirmed.")
        self.saved[state["run_id"]] = deepcopy(state)
        return 2


@pytest.fixture
def services():
    return Services(FakeTools(), FakeLLM(), FakeMemory())


@pytest.fixture
def runtime(services):
    return AgentRuntime(services)


@pytest.fixture
def settings():
    return Settings(openrouter_api_key="test-model-key", qdrant_api_key="test-memory-key", qdrant_url="http://localhost:6333")


def finish(runtime, view):
    for _ in range(20):
        if view["status"] != "paused":
            return view
        view = runtime.continue_trip(view["run_id"])
    raise AssertionError("Graph did not finish")
