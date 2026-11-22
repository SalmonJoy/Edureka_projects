"""One in-memory store per API app. Restarting the server restores the seed data."""

from copy import deepcopy

from fastapi import HTTPException, Request

from backend.data import ACTIVITIES, DESTINATIONS, HOTELS
from backend.models import Activity, Destination, Hotel


class TravelStore:
    def __init__(self) -> None:
        # Copies keep runtime changes separate from the original sample data.
        self.destinations = deepcopy(DESTINATIONS)
        self.hotels = deepcopy(HOTELS)
        self.activities = deepcopy(ACTIVITIES)

    def get_destination(self, destination_id: str) -> Destination:
        for destination in self.destinations:
            if destination.id == destination_id:
                return destination
        raise HTTPException(status_code=404, detail="Destination not found.")

    def get_hotel(self, hotel_id: str) -> Hotel:
        for hotel in self.hotels:
            if hotel.id == hotel_id:
                return hotel
        raise HTTPException(status_code=404, detail="Hotel not found.")

    def get_activity(self, activity_id: str) -> Activity:
        for activity in self.activities:
            if activity.id == activity_id:
                return activity
        raise HTTPException(status_code=404, detail="Activity not found.")

    def reset_availability(self) -> None:
        for hotel, seed in zip(self.hotels, HOTELS):
            hotel.available = seed.available
        for activity, seed in zip(self.activities, ACTIVITIES):
            activity.available = seed.available


def get_store(request: Request) -> TravelStore:
    """FastAPI injects this app's store into each route using Depends."""
    return request.app.state.store
