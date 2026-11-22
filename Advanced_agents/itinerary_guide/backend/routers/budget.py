from typing import Annotated

from fastapi import APIRouter, Depends

from backend.models import BudgetRequest, BudgetResponse
from backend.services import calculate_budget
from backend.store import TravelStore, get_store

router = APIRouter(tags=["Budget Calculator"])


@router.post("/budget/calculate", response_model=BudgetResponse)
def budget(trip: BudgetRequest, store: Annotated[TravelStore, Depends(get_store)]):
    """Resolve catalog prices and recheck availability before calculating."""
    return calculate_budget(store, trip)
