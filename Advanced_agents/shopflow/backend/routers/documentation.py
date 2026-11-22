from typing import Annotated

from fastapi import APIRouter, Depends

from backend.models import Document, DocumentStatus, DocumentUpdate, NotesUpdate, ReleaseNotes
from backend.store import ReleaseStore, find_item, get_store

router = APIRouter(prefix="/releases/{release_id}", tags=["Documentation"])
Store = Annotated[ReleaseStore, Depends(get_store)]


@router.get("/documents", response_model=list[Document])
def documents(release_id: str, store: Store, status: DocumentStatus | None = None, required: bool | None = None):
    return [item for item in store.get_release(release_id).documents
            if (status is None or item.status == status) and (required is None or item.required == required)]


@router.patch("/documents/{document_id}", response_model=Document)
def update_document(release_id: str, document_id: str, update: DocumentUpdate, store: Store):
    item = find_item(store.get_release(release_id).documents, document_id)
    item.status = update.status
    return item


@router.get("/release-notes", response_model=ReleaseNotes)
def release_notes(release_id: str, store: Store):
    return store.get_release(release_id).release_notes


@router.patch("/release-notes", response_model=ReleaseNotes)
def update_notes(release_id: str, update: NotesUpdate, store: Store):
    notes = store.get_release(release_id).release_notes
    if update.status is not None:
        notes.status = update.status
    if update.content is not None:
        notes.content = update.content
    return notes
