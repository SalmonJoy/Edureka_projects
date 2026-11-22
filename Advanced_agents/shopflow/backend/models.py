"""Pydantic models document the JSON API and validate simulator updates."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Environment = Literal["staging", "production"]
Module = Literal["catalog", "cart", "checkout", "payments", "inventory", "orders"]
Severity = Literal["critical", "high", "medium", "low"]
TestStatus = Literal["passed", "failed", "skipped"]
BugStatus = Literal["open", "in_progress", "resolved"]
CheckStatus = Literal["passed", "failed", "not_run"]
FindingStatus = Literal["open", "resolved"]
DocumentStatus = Literal["draft", "in_review", "complete"]
NotesStatus = Literal["draft", "in_review", "approved"]
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Release(Model):
    id: str
    version: str
    title: str
    description: str
    initial_scenario: Literal["ready", "needs_attention", "blocked"]


class TestResult(Model):
    id: str
    release_id: str
    name: str
    module: Module
    suite: Literal["unit", "integration", "e2e"]
    critical: bool
    status: TestStatus
    duration_ms: int = Field(ge=0)
    failure_reason: str | None = None


class TestUpdate(Model):
    status: TestStatus
    failure_reason: Nonempty | None = None

    @model_validator(mode="after")
    def validate_failure(self):
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("A failed test needs a failure_reason.")
        if self.status != "failed":
            self.failure_reason = None
        return self


class Bug(Model):
    id: str
    release_id: str
    title: str
    description: str
    module: Module
    severity: Severity
    status: BugStatus


class BugUpdate(Model):
    status: BugStatus | None = None
    severity: Severity | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.status is None and self.severity is None:
            raise ValueError("Provide status or severity.")
        return self


class SecurityCheck(Model):
    id: str
    release_id: str
    name: str
    required: bool
    status: CheckStatus
    details: str


class CheckUpdate(Model):
    status: CheckStatus
    details: Nonempty


class SecurityFinding(Model):
    id: str
    release_id: str
    check_id: str
    title: str
    description: str
    severity: Severity
    status: FindingStatus


class FindingUpdate(Model):
    status: FindingStatus | None = None
    severity: Severity | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.status is None and self.severity is None:
            raise ValueError("Provide status or severity.")
        return self


class Document(Model):
    id: str
    release_id: str
    title: str
    description: str
    required: bool
    status: DocumentStatus


class DocumentUpdate(Model):
    status: DocumentStatus


class ReleaseNotes(Model):
    release_id: str
    status: NotesStatus
    content: str


class NotesUpdate(Model):
    status: NotesStatus | None = None
    content: Nonempty | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.status is None and self.content is None:
            raise ValueError("Provide status or content.")
        return self


class ReleaseEvidence(Model):
    release: Release
    tests: list[TestResult]
    bugs: list[Bug]
    checks: list[SecurityCheck]
    findings: list[SecurityFinding]
    documents: list[Document]
    release_notes: ReleaseNotes


class TestSummary(Model):
    total: int
    passed: int
    failed: int
    skipped: int
    pass_rate: float
    critical_total: int
    critical_passed: int


class BugSummary(Model):
    total: int
    unresolved: int
    resolved: int
    unresolved_by_severity: dict[str, int]


class SecuritySummary(Model):
    total_checks: int
    passed_checks: int
    failed_checks: int
    not_run_checks: int
    required_checks: int
    required_passed: int
    total_findings: int
    unresolved_findings: int
    open_findings_by_severity: dict[str, int]


class SecurityReport(SecuritySummary):
    release_id: str
    checks: list[SecurityCheck]
    findings: list[SecurityFinding]


class DocumentSummary(Model):
    total: int
    complete: int
    required_total: int
    required_complete: int
    gaps: list[str]


class DeploymentRules(Model):
    environment: Environment
    min_test_pass_rate: int
    all_critical_tests_passed: bool = True
    blocked_bug_severities: list[Severity]
    blocked_finding_severities: list[Severity]
    required_security_checks_passed: bool = True
    require_complete_docs: bool
    require_approved_notes: bool


class DeploymentCheckRequest(Model):
    environment: Environment


class GateResult(Model):
    id: str
    name: str
    passed: bool
    expected: str
    actual: str
    reason: str


class DeploymentResult(Model):
    release_id: str
    environment: Environment
    ready: bool
    passed_gates: int
    total_gates: int
    gates: list[GateResult]
    warnings: list[str]


class ReleaseSummary(Model):
    release: Release
    tests: TestSummary
    bugs: BugSummary
    security: SecuritySummary
    documentation: DocumentSummary
    release_notes_status: NotesStatus
    readiness: dict[Environment, bool]
