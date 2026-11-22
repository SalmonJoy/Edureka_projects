"""Python checks enforce scope, provenance, policy outcome, and crew coverage."""

from pydantic import BaseModel, ValidationError

from release_crew.context import current_session
from release_crew.errors import ReviewError
from release_crew.evidence import EvidenceSession, ROLES
from release_crew.models import ManagerReport, SpecialistAssessment


def parse_output(output, schema):
    typed = getattr(output, "pydantic", None)
    if isinstance(typed, schema):
        return typed
    if isinstance(typed, BaseModel):
        return schema.model_validate(typed.model_dump())
    raw = getattr(output, "raw", "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return schema.model_validate_json(raw)


def validate_scope(model, session: EvidenceSession) -> None:
    if model.release_id != session.request.release_id or model.environment != session.request.environment:
        raise ValueError("Use the selected release_id and environment exactly.")


def validate_refs(refs: list[str], allowed: set[str]) -> None:
    missing = set(refs) - allowed
    if missing:
        raise ValueError("Unsupported evidence references: " + ", ".join(sorted(missing)))


def validate_assessment(report: SpecialistAssessment, session: EvidenceSession, domain: str) -> None:
    if session.failure:
        raise session.failure
    validate_scope(report, session)
    if report.domain != domain:
        raise ValueError(f"The assessment domain must be {domain}.")
    if domain not in session.served_domains or domain not in session.delegated_domains:
        raise ValueError(f"The release manager must delegate to {ROLES[domain]}, who must use their evidence tool.")
    allowed = session.refs_for(domain)
    validate_refs(report.evidence_refs, allowed)
    for finding in report.findings:
        validate_refs(finding.evidence_refs, allowed)


def validate_manager(report: ManagerReport, session: EvidenceSession) -> None:
    if session.failure:
        raise session.failure
    validate_scope(report, session)
    if set(session.assessments) != set(ROLES):
        raise ValueError("All three validated specialist assessments are required.")
    if report.recommendation != session.check.recommendation:
        raise ValueError(f"The policy outcome is {session.check.recommendation}; do not override deployment gates or API warnings.")
    allowed = session.all_refs()
    validate_refs(report.evidence_refs, allowed)
    issues = []
    for domain in ROLES:
        if not set(report.evidence_refs) & session.refs_for(domain):
            issues.append(f"Cite at least one source from the {domain} assessment.")
    for finding in [*report.blockers, *report.advisory_risks]:
        validate_refs(finding.evidence_refs, allowed)
    for action in report.actions:
        validate_refs(action.evidence_refs, allowed)
    failed_gates = {f"gate:{gate.id}" for gate in session.check.gates if not gate.passed}
    blocker_refs = {ref for finding in report.blockers for ref in finding.evidence_refs}
    if failed_gates - blocker_refs:
        issues.append("Include every failed gate in blockers. Missing references: " + ", ".join(sorted(failed_gates - blocker_refs)))
    if not failed_gates and report.blockers:
        raise ValueError("Passing policy gates permit advisory risks, not policy blockers.")
    if failed_gates and not report.actions:
        raise ValueError("A blocked release needs at least one actionable next step.")
    if session.check.warnings and not report.advisory_risks:
        raise ValueError("Include the permitted policy gaps as advisory risks.")
    if any(item.impact != "blocker" for item in report.blockers):
        raise ValueError("Every blocker must have impact=blocker.")
    if any(item.impact == "blocker" for item in report.advisory_risks):
        raise ValueError("Advisory risks cannot have blocker impact.")
    if issues:
        raise ValueError(" ".join(issues))


def guard_specialist(output, domain: str):
    session = current_session()
    try:
        report = parse_output(output, SpecialistAssessment)
        validate_assessment(report, session, domain)
        session.assessments[domain] = report
        session.event(ROLES[domain], "assessment_validated", report.summary)
        return True, report.model_dump_json()
    except (ValueError, ReviewError) as error:
        feedback = validation_feedback(error)
        feedback += " Allowed references: " + ", ".join(sorted(session.refs_for(domain)))
        session.event(ROLES[domain], "guardrail_rejected", feedback)
        return False, feedback


def qa_guardrail(output):
    return guard_specialist(output, "qa")


def security_guardrail(output):
    return guard_specialist(output, "security")


def documentation_guardrail(output):
    return guard_specialist(output, "documentation")


def manager_guardrail(output):
    session = current_session()
    try:
        report = parse_output(output, ManagerReport)
        validate_manager(report, session)
        return True, report.model_dump_json()
    except (ValueError, ReviewError) as error:
        feedback = validation_feedback(error)
        feedback += " Required domain sources: " + ", ".join(session.prefix + path for path in ("/tests/summary", "/security/report", "/documents"))
        session.event("Release manager", "guardrail_rejected", feedback)
        return False, feedback


def validation_feedback(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, issue['loc']))}: {issue['msg']}" for issue in error.errors(include_input=False))
    return str(error)
