"""Pydantic models describe API inputs and outputs, including validation."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Destination(BaseModel):
    id: str
    name: str
    description: str


class DestinationSummary(Destination):
    hotel_price_from: int
    activity_price_from: int
    currency: Literal["INR"] = "INR"


class Hotel(BaseModel):
    id: str
    destination_id: str
    name: str
    description: str
    price_per_night: int
    available: bool = True
    currency: Literal["INR"] = "INR"


class Activity(BaseModel):
    id: str
    destination_id: str
    name: str
    description: str
    price_per_person: int
    available: bool = True
    currency: Literal["INR"] = "INR"


class AvailabilityUpdate(BaseModel):
    available: bool = Field(strict=True)


class BudgetRequest(BaseModel):
    destination_id: str
    hotel_id: str
    activity_ids: list[str] = Field(default_factory=list)
    nights: int = Field(default=1, ge=1, strict=True)
    rooms: int = Field(default=1, ge=1, strict=True)
    travelers: int = Field(default=1, ge=1, strict=True)
    food_total: int = Field(default=0, ge=0, strict=True)
    transport_total: int = Field(default=0, ge=0, strict=True)
    misc_total: int = Field(default=0, ge=0, strict=True)

    @field_validator("activity_ids")
    @classmethod
    def reject_duplicate_activities(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Select each activity only once.")
        return value


class CostLine(BaseModel):
    id: str
    name: str
    unit_price: int
    quantity: int
    total: int


class BudgetResponse(BaseModel):
    destination: str
    currency: Literal["INR"] = "INR"
    hotel: CostLine
    activities: list[CostLine]
    hotel_total: int
    activities_total: int
    food_total: int
    transport_total: int
    misc_total: int
    total: int
    per_traveler: float
