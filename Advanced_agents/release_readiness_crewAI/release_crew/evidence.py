"""Run-scoped evidence capture, provenance checks, and a concise activity trace."""

import json
from datetime import datetime, timezone
from threading import RLock

from pydantic import ValidationError

from release_crew.client import ShopflowClient
from release_crew.errors import ReviewError
from release_crew.models import EvidenceRecord, PolicyCheck, ReleaseInfo, ReviewRequest, TraceEvent

ROLES = {"qa": "QA engineer", "security": "Security analyst", "documentation": "Documentation specialist"}
DOMAIN_GATES = {
    "qa": {"test_pass_rate", "critical_tests", "bugs"},
    "security": {"security_findings", "security_checks"},
    "documentation": {"documentation", "release_notes"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(data) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class EvidenceSession:
    def __init__(self, client: ShopflowClient, request: ReviewRequest):
        self.client = client
        self.request = request
        self.prefix = client.prefix(request.release_id)
        self.records: dict[str, EvidenceRecord] = {}
        self.bundles: dict[str, dict] = {}
        self.served_domains: set[str] = set()
        self.delegated_domains: set[str] = set()
        self.activity: list[TraceEvent] = []
        self.assessments: dict[str, object] = {}
        self.failure: ReviewError | None = None
        self.lock = RLock()

    def event(self, agent: str, operation: str, detail: str) -> None:
        with self.lock:
            self.activity.append(TraceEvent(number=len(self.activity) + 1, agent=agent, operation=operation, detail=detail))

    def record(self, source: str, data, *, method="GET"):
        self.records[source] = EvidenceRecord(source=source, method=method, captured_at=utc_now(), response=data)
        return data

    def fetch(self, suffix: str):
        source = self.prefix + suffix
        return self.record(source, self.client.read(source))

    def prepare(self) -> None:
        try:
            self.release = ReleaseInfo.model_validate(self.fetch(""))
            if self.release.id != self.request.release_id:
                raise ValueError("Wrong release")
            rules_source = f"/deployment-rules/{self.request.environment}"
            self.policy = self.record(rules_source, self.client.read(rules_source))
            if self.policy["environment"] != self.request.environment:
                raise ValueError("Wrong environment")
            self.check = self.client.check(self.request)
            self.record(self.prefix + "/deployment-check", self.check.model_dump(), method="POST")
        except (ValidationError, KeyError, TypeError, ValueError):
            raise ReviewError("Shopflow returned incomplete or mismatched review context.") from None
        self.event("Release manager", "review_started", f"Reviewing {self.release.version} for {self.request.environment}; expected policy outcome is {self.check.recommendation}.")

    def domain_bundle(self, domain: str) -> dict:
        if domain not in ROLES:
            raise ReviewError("Unknown specialist domain.")
        with self.lock:
            if domain not in self.bundles:
                if domain == "qa":
                    data = {"tests": self.fetch("/tests"), "summary": self.fetch("/tests/summary"), "bugs": self.fetch("/bugs")}
                    paths = ["/tests", "/tests/summary", "/bugs"]
                    records = [*data["tests"], *data["bugs"]]
                elif domain == "security":
                    data = self.fetch("/security/report")
                    paths = ["/security/report"]
                    records = [*data["checks"], *data["findings"]]
                else:
                    data = {"documents": self.fetch("/documents"), "release_notes": self.fetch("/release-notes")}
                    paths = ["/documents", "/release-notes"]
                    records = [*data["documents"], data["release_notes"]]
                if not records or any(item.get("release_id") != self.request.release_id for item in records):
                    raise ReviewError("Specialist evidence is empty or belongs to another release.")
                refs = [self.prefix + path for path in paths]
                refs.extend(item["id"] for item in records if "id" in item)
                refs.extend(f"gate:{gate.id}" for gate in self.check.gates if gate.id in DOMAIN_GATES[domain])
                self.bundles[domain] = {
                    "domain": domain, "release_id": self.request.release_id,
                    "environment": self.request.environment, "data": data,
                    "deployment_rules": self.policy, "valid_evidence_refs": refs,
                    "policy_gates": [gate.model_dump() for gate in self.check.gates if gate.id in DOMAIN_GATES[domain]],
                }
            self.served_domains.add(domain)
            self.event(ROLES[domain], "evidence_read", f"Consumed {domain} evidence for the selected release.")
            return self.bundles[domain]

    def refs_for(self, domain: str) -> set[str]:
        return set(self.bundles.get(domain, {}).get("valid_evidence_refs", []))

    def all_refs(self) -> set[str]:
        return set(self.records) | {f"gate:{gate.id}" for gate in self.check.gates} | set().union(*(self.refs_for(domain) for domain in ROLES))

    def recheck(self) -> None:
        for record in list(self.records.values()):
            current = self.client.check(self.request).model_dump() if record.method == "POST" else self.client.read(record.source)
            if canonical(current) != canonical(record.response):
                raise ReviewError("Shopflow evidence changed during this review. Start a new review to publish a current report.")
        self.event("Release manager", "evidence_rechecked", "Consumed evidence and deployment gates are unchanged.")
