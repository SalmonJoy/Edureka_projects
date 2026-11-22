"""A separate in-memory store per application; no database or global mutations."""

from copy import deepcopy
from typing import TypeVar

from fastapi import HTTPException, Request

from backend.data import SEED_RELEASES
from backend.models import ReleaseEvidence

ItemT = TypeVar("ItemT")


class ReleaseStore:
    def __init__(self):
        self.releases = deepcopy(SEED_RELEASES)

    def get_release(self, release_id: str) -> ReleaseEvidence:
        if release_id not in self.releases:
            raise HTTPException(status_code=404, detail="Release not found.")
        return self.releases[release_id]

    def reset(self, release_id: str) -> None:
        self.get_release(release_id)
        self.releases[release_id] = deepcopy(SEED_RELEASES[release_id])


def find_item(items: list[ItemT], item_id: str) -> ItemT:
    for item in items:
        if getattr(item, "id") == item_id:
            return item
    raise HTTPException(status_code=404, detail="Item not found in this release.")


def get_store(request: Request) -> ReleaseStore:
    return request.app.state.store
