"""Specialists have one read-only evidence tool each, fixed to the chosen scope."""

import json
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, PrivateAttr

from release_crew.errors import ReviewError
from release_crew.evidence import EvidenceSession


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceTool(BaseTool):
    args_schema: type[BaseModel] = NoArguments
    _session: EvidenceSession = PrivateAttr()
    _domain: str = PrivateAttr()

    def __init__(self, session: EvidenceSession, domain: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._session = session
        self._domain = domain

    def _run(self) -> str:
        try:
            return json.dumps(self._session.domain_bundle(self._domain), ensure_ascii=False)
        except ReviewError as error:
            self._session.failure = error
            raise
        except (ValueError, KeyError, TypeError):
            error = ReviewError("Shopflow returned malformed specialist evidence. No report was published.")
            self._session.failure = error
            raise error from None


def specialist_tool(session: EvidenceSession, domain: str) -> EvidenceTool:
    descriptions = {
        "qa": "Read test results, test summary, and bugs for the selected release. No arguments. Includes valid evidence references and relevant policy gates.",
        "security": "Read security check execution and vulnerability findings for the selected release. No arguments. Includes valid references and relevant policy gates.",
        "documentation": "Read required documents and release notes for the selected release. No arguments. Includes valid references and relevant policy gates.",
    }
    return EvidenceTool(
        session, domain, name=f"read_{domain}_evidence", description=descriptions[domain],
    )
