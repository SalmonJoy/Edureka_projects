from typing import Annotated

from fastapi import APIRouter, Depends

from backend.models import Release, ReleaseSummary
from backend.services import release_summary
from backend.store import ReleaseStore, get_store

router = APIRouter(tags=["Releases"])
Store = Annotated[ReleaseStore, Depends(get_store)]


@router.get("/releases", response_model=list[Release])
def releases(store: Store):
    return [evidence.release for evidence in store.releases.values()]


@router.get("/releases/{release_id}", response_model=Release)
def release(release_id: str, store: Store):
    return store.get_release(release_id).release


@router.get("/releases/{release_id}/summary", response_model=ReleaseSummary)
def summary(release_id: str, store: Store):
    """Current evidence summary and staging/production readiness, derived on demand."""
    return release_summary(store.get_release(release_id))


@router.post("/releases/{release_id}/reset")
def reset(release_id: str, store: Store) -> dict[str, str]:
    store.reset(release_id)
    return {"release_id": release_id, "message": "Selected release restored to its sample data."}
