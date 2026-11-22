from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend import services
from backend.models import Activity, DestinationSummary, Hotel
from backend.store import TravelStore, get_store

router = APIRouter(tags=["Travel Catalog"])
Store = Annotated[TravelStore, Depends(get_store)]


@router.get("/destinations", response_model=list[DestinationSummary])
def destinations(store: Store, q: str = ""):
    """Search destination names and descriptions. Starting prices include all items."""
    return services.search_destinations(store, q)


@router.get("/hotels", response_model=list[Hotel])
def hotels(
    store: Store, destination_id: str | None = None, q: str = "",
    max_price: Annotated[int | None, Query(ge=0)] = None,
    available_only: bool = False,
):
    """Prices are INR per room per night. Filters can be combined."""
    return services.search_hotels(store, destination_id, q, max_price, available_only)


@router.get("/activities", response_model=list[Activity])
def activities(
    store: Store, destination_id: str | None = None, q: str = "",
    max_price: Annotated[int | None, Query(ge=0)] = None,
    available_only: bool = False,
):
    """Prices are INR per person for one participation in each activity."""
    return services.search_activities(store, destination_id, q, max_price, available_only)
