from typing import Annotated

from fastapi import APIRouter, Depends

from backend.models import CheckStatus, CheckUpdate, FindingStatus, FindingUpdate, SecurityCheck, SecurityFinding, SecurityReport, Severity
from backend.services import security_report
from backend.store import ReleaseStore, find_item, get_store

router = APIRouter(prefix="/releases/{release_id}/security", tags=["Security"])
Store = Annotated[ReleaseStore, Depends(get_store)]


@router.get("/report", response_model=SecurityReport)
def report(release_id: str, store: Store):
    return security_report(store.get_release(release_id))


@router.get("/checks", response_model=list[SecurityCheck])
def checks(release_id: str, store: Store, status: CheckStatus | None = None):
    return [item for item in store.get_release(release_id).checks if status is None or item.status == status]


@router.patch("/checks/{check_id}", response_model=SecurityCheck)
def update_check(release_id: str, check_id: str, update: CheckUpdate, store: Store):
    item = find_item(store.get_release(release_id).checks, check_id)
    item.status = update.status
    item.details = update.details
    return item


@router.get("/findings", response_model=list[SecurityFinding])
def findings(release_id: str, store: Store, status: FindingStatus | None = None, severity: Severity | None = None):
    return [item for item in store.get_release(release_id).findings
            if (status is None or item.status == status) and (severity is None or item.severity == severity)]


@router.patch("/findings/{finding_id}", response_model=SecurityFinding)
def update_finding(release_id: str, finding_id: str, update: FindingUpdate, store: Store):
    item = find_item(store.get_release(release_id).findings, finding_id)
    if update.status is not None:
        item.status = update.status
    if update.severity is not None:
        item.severity = update.severity
    return item
