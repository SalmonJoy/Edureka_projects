"""Derive reports and deterministic deployment gates from current evidence."""

from backend.models import (
    BugSummary, DeploymentResult, DeploymentRules, DocumentSummary, Environment,
    GateResult, ReleaseEvidence, ReleaseSummary, SecurityReport, SecuritySummary, TestSummary,
)

SEVERITIES = ("critical", "high", "medium", "low")


def summarize_tests(evidence: ReleaseEvidence) -> TestSummary:
    tests = evidence.tests
    passed = sum(item.status == "passed" for item in tests)
    critical = [item for item in tests if item.critical]
    return TestSummary(
        total=len(tests), passed=passed,
        failed=sum(item.status == "failed" for item in tests),
        skipped=sum(item.status == "skipped" for item in tests),
        pass_rate=round(100 * passed / len(tests), 2) if tests else 0,
        critical_total=len(critical), critical_passed=sum(item.status == "passed" for item in critical),
    )


def summarize_bugs(evidence: ReleaseEvidence) -> BugSummary:
    unresolved = [item for item in evidence.bugs if item.status != "resolved"]
    return BugSummary(
        total=len(evidence.bugs), unresolved=len(unresolved), resolved=len(evidence.bugs) - len(unresolved),
        unresolved_by_severity={severity: sum(item.severity == severity for item in unresolved) for severity in SEVERITIES},
    )


def summarize_security(evidence: ReleaseEvidence) -> SecuritySummary:
    required = [item for item in evidence.checks if item.required]
    unresolved = [item for item in evidence.findings if item.status == "open"]
    return SecuritySummary(
        total_checks=len(evidence.checks),
        passed_checks=sum(item.status == "passed" for item in evidence.checks),
        failed_checks=sum(item.status == "failed" for item in evidence.checks),
        not_run_checks=sum(item.status == "not_run" for item in evidence.checks),
        required_checks=len(required), required_passed=sum(item.status == "passed" for item in required),
        total_findings=len(evidence.findings), unresolved_findings=len(unresolved),
        open_findings_by_severity={severity: sum(item.severity == severity for item in unresolved) for severity in SEVERITIES},
    )


def security_report(evidence: ReleaseEvidence) -> SecurityReport:
    return SecurityReport(
        **summarize_security(evidence).model_dump(), release_id=evidence.release.id,
        checks=evidence.checks, findings=evidence.findings,
    )


def summarize_documents(evidence: ReleaseEvidence) -> DocumentSummary:
    required = [item for item in evidence.documents if item.required]
    return DocumentSummary(
        total=len(evidence.documents), complete=sum(item.status == "complete" for item in evidence.documents),
        required_total=len(required), required_complete=sum(item.status == "complete" for item in required),
        gaps=[item.id for item in required if item.status != "complete"],
    )


def deployment_rules(environment: Environment) -> DeploymentRules:
    production = environment == "production"
    return DeploymentRules(
        environment=environment, min_test_pass_rate=100 if production else 80,
        blocked_bug_severities=["critical", "high"] if production else ["critical"],
        blocked_finding_severities=["critical", "high"] if production else ["critical"],
        require_complete_docs=production, require_approved_notes=production,
    )


def check_deployment(evidence: ReleaseEvidence, environment: Environment) -> DeploymentResult:
    rules = deployment_rules(environment)
    tests = summarize_tests(evidence)
    docs = summarize_documents(evidence)
    gates: list[GateResult] = []
    warnings = []

    def add(gate_id: str, name: str, passed: bool, expected: str, actual: str, reason: str):
        gates.append(GateResult(id=gate_id, name=name, passed=passed, expected=expected, actual=actual, reason=reason))

    # Compare integer counts, so rounding a displayed percentage cannot pass a gate.
    pass_rate_ok = tests.total > 0 and tests.passed * 100 >= rules.min_test_pass_rate * tests.total
    add("test_pass_rate", "Test pass rate", pass_rate_ok,
        f"At least {rules.min_test_pass_rate}% passed, with at least one test; skipped tests count in total.",
        f"{tests.passed}/{tests.total} passed ({tests.pass_rate}%); {tests.failed} failed, {tests.skipped} skipped.",
        "Pass-rate requirement met." if pass_rate_ok else "Insufficient passing test evidence.")

    critical_ok = tests.critical_total > 0 and tests.critical_passed == tests.critical_total
    add("critical_tests", "Critical tests", critical_ok,
        "At least one critical test and every critical test passed.",
        f"{tests.critical_passed}/{tests.critical_total} critical tests passed.",
        "Critical flows passed." if critical_ok else "A critical test failed, was skipped, or is missing.")

    bugs = [item for item in evidence.bugs if item.status != "resolved" and item.severity in rules.blocked_bug_severities]
    add("bugs", "Unresolved bugs", not bugs,
        "No unresolved " + "/".join(rules.blocked_bug_severities) + " bugs.",
        ", ".join(f"{item.id} ({item.severity}, {item.status})" for item in bugs) or "None blocking.",
        "Bug gate passed." if not bugs else "Resolve or reassess the listed bugs.")

    findings = [item for item in evidence.findings if item.status == "open" and item.severity in rules.blocked_finding_severities]
    add("security_findings", "Security findings", not findings,
        "No open " + "/".join(rules.blocked_finding_severities) + " findings.",
        ", ".join(f"{item.id} ({item.severity})" for item in findings) or "None blocking.",
        "Finding gate passed." if not findings else "Resolve or reassess the listed findings.")

    required_checks = [item for item in evidence.checks if item.required]
    missing_checks = [item for item in required_checks if item.status != "passed"]
    checks_ok = bool(required_checks) and not missing_checks
    add("security_checks", "Required security checks", checks_ok,
        "Required checks exist and all completed successfully; findings are evaluated separately.",
        ", ".join(f"{item.id} ({item.status})" for item in missing_checks) or f"{len(required_checks)} required checks passed.",
        "Security execution evidence is complete." if checks_ok else "Required scan/check evidence is incomplete.")

    docs_complete = docs.required_total > 0 and docs.required_complete == docs.required_total
    docs_ok = docs_complete or not rules.require_complete_docs
    add("documentation", "Required documentation", docs_ok,
        "Every required document complete." if rules.require_complete_docs else "Documentation gaps are advisory in staging.",
        f"{docs.required_complete}/{docs.required_total} required documents complete; gaps: {', '.join(docs.gaps) or 'none'}.",
        "Documentation policy satisfied." if docs_ok else "Complete the required guides and runbooks.")
    if not rules.require_complete_docs and not docs_complete:
        warnings.append("Required documentation is incomplete; staging permits this gap.")

    approved = evidence.release_notes.status == "approved"
    notes_ok = approved or not rules.require_approved_notes
    add("release_notes", "Release notes", notes_ok,
        "Release notes approved." if rules.require_approved_notes else "Unapproved notes are advisory in staging.",
        evidence.release_notes.status,
        "Release-note policy satisfied." if notes_ok else "Review and approve the release notes.")
    if not rules.require_approved_notes and not approved:
        warnings.append("Release notes are not approved; staging permits this gap.")

    return DeploymentResult(
        release_id=evidence.release.id, environment=environment,
        ready=all(gate.passed for gate in gates), passed_gates=sum(gate.passed for gate in gates),
        total_gates=len(gates), gates=gates, warnings=warnings,
    )


def release_summary(evidence: ReleaseEvidence) -> ReleaseSummary:
    return ReleaseSummary(
        release=evidence.release, tests=summarize_tests(evidence), bugs=summarize_bugs(evidence),
        security=summarize_security(evidence), documentation=summarize_documents(evidence),
        release_notes_status=evidence.release_notes.status,
        readiness={environment: check_deployment(evidence, environment).ready for environment in ("staging", "production")},
    )
