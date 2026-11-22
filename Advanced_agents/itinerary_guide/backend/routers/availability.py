from typing import Annotated

from fastapi import APIRouter, Depends

from backend.models import Activity, AvailabilityUpdate, Hotel
from backend.store import TravelStore, get_store

router = APIRouter(tags=["Availability Simulator"])
Store = Annotated[TravelStore, Depends(get_store)]


@router.patch("/hotels/{hotel_id}/availability", response_model=Hotel)
def hotel_availability(hotel_id: str, update: AvailabilityUpdate, store: Store):
    hotel = store.get_hotel(hotel_id)
    hotel.available = update.available
    return hotel


@router.patch("/activities/{activity_id}/availability", response_model=Activity)
def activity_availability(activity_id: str, update: AvailabilityUpdate, store: Store):
    activity = store.get_activity(activity_id)
    activity.available = update.available
    return activity


@router.post("/availability/reset")
def reset_availability(store: Store) -> dict[str, str]:
    """Reset to sample availability, which includes some unavailable items."""
    store.reset_availability()
    return {"message": "Sample availability restored."}
