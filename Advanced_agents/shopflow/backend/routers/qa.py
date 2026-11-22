from typing import Annotated

from fastapi import APIRouter, Depends

from backend.models import Bug, BugStatus, BugUpdate, Module, Severity, TestResult, TestStatus, TestSummary, TestUpdate
from backend.services import summarize_tests
from backend.store import ReleaseStore, find_item, get_store

router = APIRouter(prefix="/releases/{release_id}", tags=["QA and bugs"])
Store = Annotated[ReleaseStore, Depends(get_store)]


@router.get("/tests", response_model=list[TestResult])
def tests(release_id: str, store: Store, status: TestStatus | None = None,
          module: Module | None = None, critical: bool | None = None):
    return [item for item in store.get_release(release_id).tests
            if (status is None or item.status == status)
            and (module is None or item.module == module)
            and (critical is None or item.critical == critical)]


@router.get("/tests/summary", response_model=TestSummary)
def test_summary(release_id: str, store: Store):
    """Pass rate includes skipped tests in the denominator."""
    return summarize_tests(store.get_release(release_id))


@router.patch("/tests/{test_id}", response_model=TestResult)
def update_test(release_id: str, test_id: str, update: TestUpdate, store: Store):
    item = find_item(store.get_release(release_id).tests, test_id)
    item.status = update.status
    item.failure_reason = update.failure_reason
    if update.status == "skipped":
        item.duration_ms = 0
    return item


@router.get("/bugs", response_model=list[Bug])
def bugs(release_id: str, store: Store, status: BugStatus | None = None,
         severity: Severity | None = None, module: Module | None = None, unresolved_only: bool = False):
    """Unresolved means open or in_progress. Filters are combined."""
    return [item for item in store.get_release(release_id).bugs
            if (status is None or item.status == status)
            and (severity is None or item.severity == severity)
            and (module is None or item.module == module)
            and (not unresolved_only or item.status != "resolved")]


@router.patch("/bugs/{bug_id}", response_model=Bug)
def update_bug(release_id: str, bug_id: str, update: BugUpdate, store: Store):
    item = find_item(store.get_release(release_id).bugs, bug_id)
    if update.status is not None:
        item.status = update.status
    if update.severity is not None:
        item.severity = update.severity
    return item
