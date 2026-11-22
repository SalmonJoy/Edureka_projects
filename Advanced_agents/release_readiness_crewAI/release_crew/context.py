"""Module-level SDK callbacks find only their current run, never another session."""

from contextlib import contextmanager
from contextvars import ContextVar

from release_crew.errors import ReviewError
from release_crew.evidence import EvidenceSession

_active_session: ContextVar[EvidenceSession | None] = ContextVar("readiness_session", default=None)


def current_session() -> EvidenceSession:
    session = _active_session.get()
    if session is None:
        raise ReviewError("No active review context is available.")
    return session


@contextmanager
def bind_session(session: EvidenceSession):
    token = _active_session.set(session)
    try:
        yield
    finally:
        _active_session.reset(token)
