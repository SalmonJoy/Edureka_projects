"""One review per call. Streamlit is only a caller, never the orchestration engine."""

from uuid import uuid4

from release_crew.client import ShopflowClient
from release_crew.config import Settings
from release_crew.context import bind_session
from release_crew.errors import ReviewError
from release_crew.evidence import EvidenceSession, ROLES, utc_now
from release_crew.models import ManagerReport, ReadinessReport, ReviewRequest, ReviewResult, SpecialistAssessment
from release_crew.validation import parse_output, validate_manager


def run_review(request: ReviewRequest, settings: Settings, *, client=None, crew_factory=None) -> ReviewResult:
    started_at = utc_now()
    run_id = str(uuid4())
    client = client if client is not None else ShopflowClient(settings.shopflow_url)
    session = EvidenceSession(client, request)
    try:
        session.prepare()
        if crew_factory is None:
            from release_crew.crew import build_crew
            crew_factory = build_crew
        from crewai.hooks.dispatch import InterceptionPoint, scoped_hooks
        from release_crew.activity import record_tool_call
        with bind_session(session), scoped_hooks({InterceptionPoint.PRE_TOOL_CALL: [record_tool_call]}):
            crew = crew_factory(session, settings)
            crew.kickoff()
        # Read the final TaskOutput, not a free-form top-level raw string.
        manager = parse_output(crew.tasks[-1].output, ManagerReport)
        validate_manager(manager, session)
        assessments = [SpecialistAssessment.model_validate(session.assessments[domain]) for domain in ROLES]
        session.recheck()
        session.event("Release manager", "report_published", f"Validated recommendation: {manager.recommendation}.")
        report = ReadinessReport(
            run_id=run_id, started_at=started_at, completed_at=utc_now(),
            release=session.release, environment=request.environment, model_name=settings.model,
            manager=manager, assessments=assessments, policy=session.policy,
            deployment_check=session.check, evidence=list(session.records.values()), activity=session.activity,
        )
        return ReviewResult(status="complete", report=report, assessments=assessments, activity=session.activity)
    except ReviewError as error:
        message = str(error)
    except Exception as error:
        # SDK failures may contain provider request details. Never expose them raw.
        message = "Crew execution or report validation did not complete. Check OpenRouter credentials, credits, model availability, and required specialist delegation."
        if session.failure:
            message = str(session.failure)
        elif "guardrail" in str(error).lower():
            message = "The crew could not produce a valid evidence-backed report after one guardrail retry. No GO recommendation was published."
    finally:
        client.close()
    session.event("Release manager", "review_incomplete", message)
    return ReviewResult(
        status="incomplete", error=message,
        assessments=[SpecialistAssessment.model_validate(value) for value in session.assessments.values()],
        activity=session.activity,
    )
