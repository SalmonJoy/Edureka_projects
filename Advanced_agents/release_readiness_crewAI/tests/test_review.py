from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from release_crew.context import bind_session, current_session
from release_crew.errors import ReviewError
from release_crew.evidence import ROLES
from release_crew.models import ManagerReport, ReviewRequest, SpecialistAssessment
from release_crew.reporting import report_markdown
from release_crew.runner import run_review
from release_crew.validation import validate_assessment, validate_manager
from conftest import FakeCrew, assessment_for, consume_domain, manager_for


@pytest.mark.parametrize("release_id,environment,expected", [
    ("rel-100", "production", "GO"), ("rel-100", "staging", "GO"),
    ("rel-110", "production", "NO_GO"), ("rel-110", "staging", "GO_WITH_WARNINGS"),
    ("rel-120", "production", "NO_GO"), ("rel-120", "staging", "NO_GO"),
])
def test_all_policy_outcomes_and_complete_handoffs(client, settings, fake_factory, release_id, environment, expected):
    request = ReviewRequest(release_id=release_id, environment=environment)
    result = run_review(request, settings, client=client, crew_factory=fake_factory)
    assert result.status == "complete", result.error
    assert result.recommendation == expected
    assert result.report is not None
    assert {item.domain for item in result.assessments} == set(ROLES)
    assert len([item for item in result.activity if item.operation == "delegated_review"]) == 3
    assert len([item for item in result.activity if item.operation == "evidence_read"]) == 3
    assert result.report.manager.recommendation == result.report.deployment_check.recommendation
    assert len(result.report.evidence) == 9
    assert result.activity[-2].operation == "evidence_rechecked"
    assert result.activity[-1].operation == "report_published"


def test_no_write_endpoints_or_unselected_releases(api, client, settings, fake_factory):
    run_review(ReviewRequest(release_id="rel-110"), settings, client=client, crew_factory=fake_factory)
    assert all(method == "GET" or path.endswith("/deployment-check") for method, path in api.calls)
    assert all("rel-100" not in path and "rel-120" not in path for _, path in api.calls)
    assert not any("reset" in path for _, path in api.calls)


def test_evidence_change_stops_publication(api, client, settings):
    def change_data():
        api.data["GET /releases/rel-110/release-notes"]["content"] = "Changed during the review"
    factory = lambda session, settings: FakeCrew(session, after=change_data)
    result = run_review(ReviewRequest(release_id="rel-110"), settings, client=client, crew_factory=factory)
    assert result.status == "incomplete" and result.report is None
    assert result.recommendation == "INCOMPLETE"
    assert result.error is not None
    assert "changed during" in result.error
    assert len(result.assessments) == 3


@pytest.mark.parametrize("missing", list(ROLES))
def test_missing_specialist_cannot_be_hidden(client, settings, missing):
    factory = lambda session, settings: FakeCrew(session, skip=missing)
    result = run_review(ReviewRequest(release_id="rel-110"), settings, client=client, crew_factory=factory)
    assert result.status == "incomplete"
    assert result.report is None
    assert result.recommendation == "INCOMPLETE"


def test_manager_cannot_override_no_go(client, settings):
    factory = lambda session, settings: FakeCrew(session, change=lambda report: report.model_copy(update={"recommendation": "GO"}))
    result = run_review(ReviewRequest(release_id="rel-110"), settings, client=client, crew_factory=factory)
    assert result.status == "incomplete" and result.report is None


@pytest.mark.parametrize("failure", ["offline", "http_error", "wrong_release", "wrong_environment", "inconsistent_gates"])
def test_api_failures_never_produce_a_report(api, client, settings, fake_factory, failure):
    if failure == "offline":
        api.offline = True
    elif failure == "http_error":
        api.status = 500
    elif failure == "wrong_release":
        api.data["GET /releases/rel-110"]["id"] = "rel-100"
    elif failure == "wrong_environment":
        api.data["POST /releases/rel-110/deployment-check production"]["environment"] = "staging"
    else:
        api.data["POST /releases/rel-110/deployment-check production"]["ready"] = True
    result = run_review(ReviewRequest(release_id="rel-110"), settings, client=client, crew_factory=fake_factory)
    assert result.status == "incomplete" and result.report is None and result.error


def test_provider_failure_is_sanitized(client, settings):
    def broken(session, settings):
        raise RuntimeError("provider raw secret from HTTP request")
    result = run_review(ReviewRequest(release_id="rel-100"), settings, client=client, crew_factory=broken)
    assert result.status == "incomplete"
    assert result.error is not None
    assert "raw secret" not in result.error
    assert result.recommendation == "INCOMPLETE"


def test_provenance_and_scope_validation(session):
    with bind_session(session):
        consume_domain(session, "qa")
        report = assessment_for(session, "qa")
        validate_assessment(report, session, "qa")
        with pytest.raises(ValueError, match="Unsupported"):
            validate_assessment(report.model_copy(update={"evidence_refs": ["BUG-999"]}), session, "qa")
        with pytest.raises(ValueError, match="selected release"):
            validate_assessment(report.model_copy(update={"release_id": "rel-100"}), session, "qa")
        session.delegated_domains.clear()
        with pytest.raises(ValueError, match="delegate"):
            validate_assessment(report, session, "qa")


def test_reference_typography_is_canonicalized_but_not_invented(session):
    with bind_session(session):
        consume_domain(session, "documentation")
        data = assessment_for(session, "documentation").model_dump()
        data["evidence_refs"] = ["`DOC\u2011001`"]
        report = SpecialistAssessment.model_validate(data)
        assert report.evidence_refs == ["DOC-001"]
        validate_assessment(report, session, "documentation")
        data["evidence_refs"] = ["DOC\u2011999"]
        with pytest.raises(ValueError, match="Unsupported"):
            validate_assessment(SpecialistAssessment.model_validate(data), session, "documentation")


def test_all_failed_gates_must_be_represented(session):
    with bind_session(session):
        for domain in ROLES:
            consume_domain(session, domain)
            session.assessments[domain] = assessment_for(session, domain)
        report = manager_for(session)
        report.blockers = []
        with pytest.raises(ValueError, match="every failed gate"):
            validate_manager(report, session)


def test_downloads_use_validated_report_without_model_calls(client, settings, fake_factory):
    result = run_review(ReviewRequest(release_id="rel-110"), settings, client=client, crew_factory=fake_factory)
    assert result.report is not None
    markdown = report_markdown(result.report)
    assert "NO_GO" in markdown and "production" in markdown
    assert "QA" in markdown and "Security" in markdown and "Documentation" in markdown
    assert "BUG-002" in markdown
    assert result.report.run_id in markdown
    restored = type(result.report).model_validate_json(result.report.model_dump_json())
    assert restored.manager.recommendation == "NO_GO"


def test_context_is_nested_and_session_scoped(session, client):
    from release_crew.evidence import EvidenceSession
    other = EvidenceSession(client, ReviewRequest(release_id="rel-100"))
    with bind_session(session):
        assert current_session() is session
        with bind_session(other):
            assert current_session() is other
        assert current_session() is session
    with pytest.raises(ReviewError, match="No active"):
        current_session()
