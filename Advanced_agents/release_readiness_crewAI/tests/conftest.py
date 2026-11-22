"""Offline API fixtures and scripted reports; no real model calls in pytest."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from release_crew.activity import record_tool_call
from release_crew.client import ShopflowClient
from release_crew.config import Settings
from release_crew.evidence import DOMAIN_GATES, EvidenceSession, ROLES
from release_crew.models import ManagerReport, ReviewRequest, SpecialistAssessment
from release_crew.tools import specialist_tool
from release_crew.validation import documentation_guardrail, manager_guardrail, qa_guardrail, security_guardrail

GUARDS = {"qa": qa_guardrail, "security": security_guardrail, "documentation": documentation_guardrail}


class FixtureAPI:
    def __init__(self):
        self.data = json.loads((Path(__file__).parent / "fixtures" / "shopflow.json").read_text())
        self.calls = []
        self.status = 200
        self.offline = False

    def handler(self, request):
        self.calls.append((request.method, request.url.path))
        if self.offline:
            raise httpx.ConnectError("Offline", request=request)
        key = request.method + " " + request.url.path
        if request.method == "POST":
            assert request.url.path.endswith("/deployment-check"), "Evidence mutation attempted"
            key += " " + json.loads(request.content)["environment"]
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "Simulated API error"})
        return httpx.Response(200, json=deepcopy(self.data[key])) if key in self.data else httpx.Response(404, json={"detail": "Not found"})


@pytest.fixture
def api():
    return FixtureAPI()


@pytest.fixture
def client(api):
    with httpx.Client(transport=httpx.MockTransport(api.handler)) as http:
        yield ShopflowClient("http://shopflow", http=http)


@pytest.fixture
def settings():
    return Settings(openrouter_api_key="offline-test-key", shopflow_url="http://shopflow")


@pytest.fixture
def session(client):
    session = EvidenceSession(client, ReviewRequest(release_id="rel-110"))
    session.prepare()
    return session


def assessment_for(session, domain):
    bundle = session.bundles[domain]
    findings = [
        {"observation": gate.actual, "impact": "blocker", "evidence_refs": [f"gate:{gate.id}"], "suggested_action": gate.reason}
        for gate in session.check.gates if gate.id in DOMAIN_GATES[domain] and not gate.passed
    ]
    return SpecialistAssessment(
        domain=domain, release_id=session.request.release_id, environment=session.request.environment,
        summary=f"Reviewed {domain} evidence under the selected environment policy.",
        findings=findings, evidence_refs=[bundle["valid_evidence_refs"][0]],
    )


def manager_for(session):
    blockers = [
        {"observation": gate.actual, "impact": "blocker", "evidence_refs": [f"gate:{gate.id}"], "suggested_action": gate.reason}
        for gate in session.check.gates if not gate.passed
    ]
    actions = [{"owner": "Release manager", "priority": "high", "description": finding["suggested_action"], "evidence_refs": finding["evidence_refs"]} for finding in blockers]
    advisory = [{"observation": warning, "impact": "advisory", "evidence_refs": ["gate:documentation"], "suggested_action": "Close the permitted documentation gaps."} for warning in session.check.warnings]
    return ManagerReport(
        release_id=session.request.release_id, environment=session.request.environment,
        recommendation=session.check.recommendation, executive_summary="Recommendation follows all three specialist assessments and the API deployment gates.",
        blockers=blockers, advisory_risks=advisory, actions=actions,
        evidence_refs=[session.bundles[domain]["valid_evidence_refs"][0] for domain in ROLES],
    )


def consume_domain(session, domain):
    record_tool_call(SimpleNamespace(
        agent=SimpleNamespace(role="Release manager"), tool_name="Delegate work to coworker",
        tool_input={"coworker": ROLES[domain]},
    ))
    specialist_tool(session, domain)._run()


class FakeCrew:
    def __init__(self, session, *, skip=None, change=None, after=None):
        self.session = session
        self.skip = skip
        self.change = change
        self.after = after
        self.tasks = [SimpleNamespace(output=None)]

    def kickoff(self):
        for domain in ROLES:
            if domain == self.skip:
                continue
            consume_domain(self.session, domain)
            output = SimpleNamespace(pydantic=assessment_for(self.session, domain))
            accepted, feedback = GUARDS[domain](output)
            if not accepted:
                raise ValueError("guardrail: " + feedback)
        report = manager_for(self.session)
        if self.change:
            report = self.change(report)
        output = SimpleNamespace(pydantic=report)
        accepted, feedback = manager_guardrail(output)
        if not accepted:
            raise ValueError("guardrail: " + feedback)
        self.tasks[-1].output = output
        if self.after:
            self.after()


@pytest.fixture
def fake_factory():
    return lambda session, settings: FakeCrew(session)
