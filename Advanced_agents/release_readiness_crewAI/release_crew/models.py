"""Small report models keep the agents' handoffs explicit and inspectable."""

from typing import Annotated, Any, Literal
import unicodedata

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, computed_field, model_validator

Environment = Literal["staging", "production"]
Domain = Literal["qa", "security", "documentation"]
Owner = Literal["Release manager", "QA engineer", "Security analyst", "Documentation specialist"]
Recommendation = Literal["GO", "GO_WITH_WARNINGS", "NO_GO"]
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def canonical_ref(value):
    if not isinstance(value, str):
        raise ValueError("Evidence references must be strings.")
    # Models sometimes typeset DOC-001 as DOC‑001. Normalize typography, then
    # still require an exact match against the actual API reference whitelist.
    return unicodedata.normalize("NFKC", value).translate(str.maketrans("‐‑‒–−", "-----")).strip().strip("`")


EvidenceRef = Annotated[str, BeforeValidator(canonical_ref)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewRequest(Model):
    release_id: Nonempty
    environment: Environment = "production"


class ReleaseInfo(BaseModel):
    id: Nonempty
    version: Nonempty
    title: Nonempty
    initial_scenario: str = ""


class Gate(Model):
    id: Nonempty
    name: Nonempty
    passed: bool = Field(strict=True)
    expected: str
    actual: str
    reason: str


class PolicyCheck(Model):
    release_id: Nonempty
    environment: Environment
    ready: bool = Field(strict=True)
    passed_gates: int = Field(ge=0, strict=True)
    total_gates: int = Field(gt=0, strict=True)
    gates: list[Gate] = Field(min_length=1)
    warnings: list[str]

    @model_validator(mode="after")
    def consistent_counts(self):
        if len(self.gates) != self.total_gates or sum(gate.passed for gate in self.gates) != self.passed_gates:
            raise ValueError("Inconsistent gate counts.")
        if len({gate.id for gate in self.gates}) != len(self.gates):
            raise ValueError("Duplicate gate IDs.")
        if self.ready != all(gate.passed for gate in self.gates):
            raise ValueError("Overall readiness disagrees with gate results.")
        return self

    @property
    def recommendation(self) -> Recommendation:
        if not self.ready:
            return "NO_GO"
        return "GO_WITH_WARNINGS" if self.warnings else "GO"


class Finding(Model):
    observation: Nonempty
    impact: Literal["blocker", "advisory", "informational"]
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    suggested_action: Nonempty


class SpecialistAssessment(Model):
    domain: Domain
    release_id: Nonempty
    environment: Environment
    summary: Nonempty
    findings: list[Finding]
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class Action(Model):
    owner: Owner
    priority: Literal["high", "medium", "low"]
    description: Nonempty
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class ManagerReport(Model):
    release_id: Nonempty
    environment: Environment
    recommendation: Recommendation
    executive_summary: Nonempty
    blockers: list[Finding]
    advisory_risks: list[Finding]
    actions: list[Action]
    evidence_refs: list[EvidenceRef] = Field(min_length=3)


class TraceEvent(Model):
    number: int
    agent: str
    operation: str
    detail: str


class EvidenceRecord(Model):
    source: str
    method: Literal["GET", "POST"]
    captured_at: str
    response: Any


class ReadinessReport(Model):
    run_id: str
    started_at: str
    completed_at: str
    release: ReleaseInfo
    environment: Environment
    model_name: str
    manager: ManagerReport
    assessments: list[SpecialistAssessment]
    policy: dict
    deployment_check: PolicyCheck
    evidence: list[EvidenceRecord]
    activity: list[TraceEvent]


class ReviewResult(Model):
    status: Literal["complete", "incomplete"]
    report: ReadinessReport | None = None
    error: str | None = None
    assessments: list[SpecialistAssessment] = Field(default_factory=list)
    activity: list[TraceEvent] = Field(default_factory=list)

    @computed_field
    @property
    def recommendation(self) -> str:
        return self.report.manager.recommendation if self.status == "complete" and self.report else "INCOMPLETE"
