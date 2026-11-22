"""Plain business logic: catalog searches and authoritative budget calculation."""

from fastapi import HTTPException

from backend.models import Activity, BudgetRequest, BudgetResponse, CostLine, DestinationSummary, Hotel
from backend.store import TravelStore


def matches_text(name: str, description: str, query: str) -> bool:
    return query.strip().casefold() in f"{name} {description}".casefold()


def search_destinations(store: TravelStore, query: str) -> list[DestinationSummary]:
    return [
        DestinationSummary(
            **destination.model_dump(),
            hotel_price_from=min(h.price_per_night for h in store.hotels if h.destination_id == destination.id),
            activity_price_from=min(a.price_per_person for a in store.activities if a.destination_id == destination.id),
        )
        for destination in store.destinations
        if matches_text(destination.name, destination.description, query)
    ]


def search_hotels(
    store: TravelStore, destination_id: str | None, query: str,
    max_price: int | None, available_only: bool,
) -> list[Hotel]:
    if destination_id is not None:
        store.get_destination(destination_id)
    return [
        hotel for hotel in store.hotels
        if (destination_id is None or hotel.destination_id == destination_id)
        and matches_text(hotel.name, hotel.description, query)
        and (max_price is None or hotel.price_per_night <= max_price)
        and (not available_only or hotel.available)
    ]


def search_activities(
    store: TravelStore, destination_id: str | None, query: str,
    max_price: int | None, available_only: bool,
) -> list[Activity]:
    if destination_id is not None:
        store.get_destination(destination_id)
    return [
        activity for activity in store.activities
        if (destination_id is None or activity.destination_id == destination_id)
        and matches_text(activity.name, activity.description, query)
        and (max_price is None or activity.price_per_person <= max_price)
        and (not available_only or activity.available)
    ]


def calculate_budget(store: TravelStore, trip: BudgetRequest) -> BudgetResponse:
    destination = store.get_destination(trip.destination_id)
    hotel = store.get_hotel(trip.hotel_id)
    activities = [store.get_activity(activity_id) for activity_id in trip.activity_ids]

    for item in [hotel, *activities]:
        if item.destination_id != destination.id:
            raise HTTPException(status_code=422, detail="All selections must belong to the selected destination.")
        if not item.available:
            raise HTTPException(status_code=409, detail=f"{item.name} is currently unavailable. Refresh your selections.")

    hotel_quantity = trip.nights * trip.rooms
    hotel_line = CostLine(
        id=hotel.id, name=hotel.name, unit_price=hotel.price_per_night,
        quantity=hotel_quantity, total=hotel.price_per_night * hotel_quantity,
    )
    activity_lines = [
        CostLine(
            id=activity.id, name=activity.name, unit_price=activity.price_per_person,
            quantity=trip.travelers, total=activity.price_per_person * trip.travelers,
        )
        for activity in activities
    ]
    activities_total = sum(line.total for line in activity_lines)
    total = hotel_line.total + activities_total + trip.food_total + trip.transport_total + trip.misc_total
    return BudgetResponse(
        destination=destination.name, hotel=hotel_line, activities=activity_lines,
        hotel_total=hotel_line.total, activities_total=activities_total,
        food_total=trip.food_total, transport_total=trip.transport_total,
        misc_total=trip.misc_total, total=total,
        per_traveler=round(total / trip.travelers, 2),
    )
