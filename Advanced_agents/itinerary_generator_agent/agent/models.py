"""Structured inputs and outputs. The LLM selects IDs, never invents prices."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TripRequest(StrictModel):
    user_id: Nonempty = "demo-user"
    destination_id: Nonempty = "jaipur"
    nights: int = Field(default=2, ge=1, le=14, strict=True)
    rooms: int = Field(default=1, ge=1, le=30, strict=True)
    travelers: int = Field(default=2, ge=1, le=30, strict=True)
    max_budget: int = Field(default=20000, gt=0, strict=True)
    food_total: int = Field(default=0, ge=0, strict=True)
    transport_total: int = Field(default=0, ge=0, strict=True)
    misc_total: int = Field(default=0, ge=0, strict=True)
    preferences: str = ""


class Destination(BaseModel):
    id: str
    name: str
    description: str


class Hotel(BaseModel):
    id: str
    destination_id: str
    name: str
    description: str
    price_per_night: int = Field(ge=0, strict=True)
    available: bool = Field(strict=True)


class Activity(BaseModel):
    id: str
    destination_id: str
    name: str
    description: str
    price_per_person: int = Field(ge=0, strict=True)
    available: bool = Field(strict=True)


class Catalog(BaseModel):
    destination: Destination
    hotels: dict[str, Hotel]
    activities: dict[str, Activity]


class DayPlan(StrictModel):
    day: int = Field(ge=1, strict=True)
    activity_ids: list[str]
    free_time: str = Field(description="Optional unpriced leisure/rest; not an invented catalog activity.")


class ItineraryPlan(StrictModel):
    hotel_id: str
    days: list[DayPlan]
    rationale: str


class PlanDecision(StrictModel):
    itinerary: ItineraryPlan | None = Field(description="Null when no feasible plan remains.")
    reason: str


class Step(StrictModel):
    kind: Literal["hotel", "activity"]
    item_id: str
    day: int | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.item_id}"


class StepResult(StrictModel):
    step: Step
    outcome: Literal["success", "unexpected", "error"]
    observation: str
    snapshot: dict = Field(default_factory=dict)


class CriterionScore(StrictModel):
    score: int = Field(ge=0, le=2, strict=True)
    reason: str


class SoftReview(StrictModel):
    preferences: CriterionScore
    pacing: CriterionScore
    improvements: list[str]


class EvaluationResult(StrictModel):
    criteria: dict[str, CriterionScore]
    total: int
    passed: bool
    issues: list[str]


class BudgetLine(BaseModel):
    id: str
    name: str
    unit_price: int = Field(ge=0, strict=True)
    quantity: int = Field(ge=1, strict=True)
    total: int = Field(ge=0, strict=True)


class BudgetQuote(BaseModel):
    destination: str
    currency: Literal["INR"]
    hotel: BudgetLine
    activities: list[BudgetLine]
    hotel_total: int = Field(ge=0, strict=True)
    activities_total: int = Field(ge=0, strict=True)
    food_total: int = Field(ge=0, strict=True)
    transport_total: int = Field(ge=0, strict=True)
    misc_total: int = Field(ge=0, strict=True)
    total: int = Field(ge=0, strict=True)
    per_traveler: float = Field(ge=0)
