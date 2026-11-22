import pytest
from fastapi.testclient import TestClient

from backend.main import create_app


def readiness(client, release_id="rel-100", environment="production"):
    response = client.post(f"/releases/{release_id}/deployment-check", json={"environment": environment})
    assert response.status_code == 200
    return response.json()


def gates(result):
    return {gate["id"]: gate for gate in result["gates"]}


def test_health_openapi_and_sample_inventory(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/releases/{release_id}/deployment-check" in schema["paths"]
    releases = client.get("/releases").json()
    assert [item["id"] for item in releases] == ["rel-100", "rel-110", "rel-120"]
    for release in releases:
        prefix = f"/releases/{release['id']}"
        for path, count in [("tests", 10), ("bugs", 4), ("security/checks", 4), ("security/findings", 3), ("documents", 4)]:
            items = client.get(f"{prefix}/{path}").json()
            assert len(items) == len({item["id"] for item in items}) == count
            assert all(item["release_id"] == release["id"] for item in items)
        report = client.get(prefix + "/security/report").json()
        assert {item["check_id"] for item in report["findings"]} <= {item["id"] for item in report["checks"]}


@pytest.mark.parametrize("release_id,environment,expected", [
    ("rel-100", "staging", True), ("rel-100", "production", True),
    ("rel-110", "staging", True), ("rel-110", "production", False),
    ("rel-120", "staging", False), ("rel-120", "production", False),
])
def test_scenario_gate_matrix(client, release_id, environment, expected):
    result = readiness(client, release_id, environment)
    assert result["ready"] is expected
    assert len(result["gates"]) == result["total_gates"] == 7
    assert result["passed_gates"] == sum(gate["passed"] for gate in result["gates"])
    assert all(gate["expected"] and gate["actual"] and gate["reason"] for gate in result["gates"])
    assert result["ready"] == all(gate["passed"] for gate in result["gates"])
    assert client.get(f"/releases/{release_id}/summary").json()["readiness"][environment] is expected


@pytest.mark.parametrize("release_id,counts", [
    ("rel-100", (10, 0, 0, 100)), ("rel-110", (8, 1, 1, 80)), ("rel-120", (4, 4, 2, 40)),
])
def test_test_summaries(client, release_id, counts):
    summary = client.get(f"/releases/{release_id}/tests/summary").json()
    assert tuple(summary[key] for key in ("passed", "failed", "skipped", "pass_rate")) == counts
    assert summary["total"] == 10
    if release_id == "rel-120":
        assert summary["critical_passed"] < summary["critical_total"]


def test_combined_test_and_bug_filters(client):
    tests = client.get("/releases/rel-120/tests", params={"status": "failed", "module": "inventory", "critical": True}).json()
    assert [item["id"] for item in tests] == ["TEST-005"]
    noncritical = client.get("/releases/rel-110/tests", params={"critical": False}).json()
    assert noncritical and all(item["critical"] is False for item in noncritical)
    bugs = client.get("/releases/rel-110/bugs", params={"status": "open", "severity": "high", "module": "inventory", "unresolved_only": True}).json()
    assert [item["id"] for item in bugs] == ["BUG-002"]
    unresolved = client.get("/releases/rel-110/bugs", params={"unresolved_only": True}).json()
    assert {item["status"] for item in unresolved} == {"open", "in_progress"}
    assert client.get("/releases/rel-100/bugs", params={"unresolved_only": True, "severity": "critical"}).json() == []


def test_security_and_document_filters(client):
    prefix = "/releases/rel-120"
    assert [item["id"] for item in client.get(prefix + "/security/checks", params={"status": "not_run"}).json()] == ["SEC-003"]
    findings = client.get(prefix + "/security/findings", params={"status": "open", "severity": "critical"}).json()
    assert [item["id"] for item in findings] == ["FIND-001"]
    docs = client.get(prefix + "/documents", params={"status": "draft", "required": True}).json()
    assert len(docs) == 3
    assert client.get(prefix + "/documents", params={"required": False}).json() == []


def test_failure_reason_validation_and_clearing(client):
    path = "/releases/rel-100/tests/TEST-001"
    assert client.patch(path, json={"status": "failed"}).status_code == 422
    assert client.patch(path, json={"status": "failed", "failure_reason": " "}).status_code == 422
    failed = client.patch(path, json={"status": "failed", "failure_reason": "Simulated regression"}).json()
    assert failed["failure_reason"] == "Simulated regression"
    assert readiness(client)["ready"] is False
    skipped = client.patch(path, json={"status": "skipped"}).json()
    assert skipped["failure_reason"] is None and skipped["duration_ms"] == 0
    assert readiness(client)["ready"] is False
    passed = client.patch(path, json={"status": "passed", "failure_reason": "Old reason"}).json()
    assert passed["failure_reason"] is None
    assert readiness(client)["ready"] is True


def test_skipped_tests_count_toward_threshold(client):
    prefix = "/releases/rel-110"
    client.patch(prefix + "/tests/TEST-010", json={"status": "skipped"})
    assert readiness(client, "rel-110", "staging")["ready"] is True  # 8/10
    client.patch(prefix + "/tests/TEST-008", json={"status": "skipped"})
    result = readiness(client, "rel-110", "staging")
    assert result["ready"] is False  # 7/10, not 7/7
    assert gates(result)["test_pass_rate"]["passed"] is False


def test_missing_evidence_does_not_pass_vacuously(client):
    evidence = client.app.state.store.get_release("rel-100")
    evidence.tests.clear()
    summary = client.get("/releases/rel-100/tests/summary").json()
    assert summary["total"] == 0 and summary["pass_rate"] == 0
    result = readiness(client)
    assert result["ready"] is False
    assert not gates(result)["test_pass_rate"]["passed"]
    evidence.checks.clear()
    evidence.documents.clear()
    result = readiness(client)
    assert gates(result)["security_checks"]["passed"] is False
    assert gates(result)["documentation"]["passed"] is False


def test_remediation_makes_attention_release_production_ready(client):
    prefix = "/releases/rel-110"
    assert readiness(client, "rel-110")["ready"] is False
    for test_id in ("TEST-009", "TEST-010"):
        client.patch(prefix + f"/tests/{test_id}", json={"status": "passed"})
    client.patch(prefix + "/bugs/BUG-002", json={"status": "resolved"})
    for document_id in ("DOC-002", "DOC-004"):
        client.patch(prefix + f"/documents/{document_id}", json={"status": "complete"})
    client.patch(prefix + "/release-notes", json={"status": "approved", "content": "Reviewed release notes."})
    assert readiness(client, "rel-110")["ready"] is True
    summary = client.get(prefix + "/summary").json()
    assert summary["tests"]["passed"] == 10
    assert summary["bugs"]["unresolved_by_severity"]["high"] == 0
    assert summary["security"]["unresolved_findings"] == 1  # Medium is permitted.


def test_security_findings_and_execution_are_independent_gates(client):
    prefix = "/releases/rel-100/security"
    client.patch(prefix + "/findings/FIND-002", json={"status": "open"})
    result = readiness(client)
    assert gates(result)["security_checks"]["passed"] is True
    assert gates(result)["security_findings"]["passed"] is False
    client.patch(prefix + "/checks/SEC-002", json={"status": "failed", "details": "Simulated scanner failure."})
    client.patch(prefix + "/findings/FIND-002", json={"status": "resolved"})
    result = readiness(client)
    assert gates(result)["security_findings"]["passed"] is True
    assert gates(result)["security_checks"]["passed"] is False
    client.patch(prefix + "/checks/SEC-002", json={"status": "passed", "details": "Simulated scan completed."})
    assert readiness(client)["ready"] is True


def test_bug_criticality_and_resolution_change_gates(client):
    path = "/releases/rel-100/bugs/BUG-004"
    assert readiness(client)["ready"] is True  # Open low-severity cosmetic bug.
    client.patch(path, json={"severity": "critical", "status": "in_progress"})
    assert readiness(client)["ready"] is False
    assert readiness(client, environment="staging")["ready"] is False
    client.patch(path, json={"status": "resolved"})
    assert readiness(client)["ready"] is True


def test_staging_document_and_notes_warnings(client):
    result = readiness(client, "rel-110", "staging")
    assert result["ready"] is True and len(result["warnings"]) == 2
    assert gates(result)["documentation"]["passed"] is True
    production = readiness(client, "rel-110")
    assert gates(production)["documentation"]["passed"] is False
    assert gates(production)["release_notes"]["passed"] is False
    assert production["warnings"] == []


def test_reset_is_release_scoped_and_factories_are_independent(client):
    before = client.get("/releases/rel-110/summary").json()
    client.patch("/releases/rel-110/tests/TEST-010", json={"status": "passed"})
    client.patch("/releases/rel-100/bugs/BUG-004", json={"status": "resolved"})
    assert client.post("/releases/rel-110/reset").status_code == 200
    assert client.get("/releases/rel-110/summary").json() == before
    assert client.get("/releases/rel-100/bugs", params={"status": "open"}).json() == []
    with TestClient(create_app()) as independent:
        assert len(independent.get("/releases/rel-100/bugs", params={"status": "open"}).json()) == 1


@pytest.mark.parametrize("suffix", ["", "/summary", "/tests", "/tests/summary", "/bugs", "/security/report", "/security/checks", "/security/findings", "/documents", "/release-notes"])
def test_unknown_release_is_404(client, suffix):
    assert client.get("/releases/missing" + suffix).status_code == 404


@pytest.mark.parametrize("suffix,body", [
    ("/tests/missing", {"status": "passed"}),
    ("/bugs/missing", {"status": "resolved"}),
    ("/security/checks/missing", {"status": "passed", "details": "Checked"}),
    ("/security/findings/missing", {"status": "resolved"}),
    ("/documents/missing", {"status": "complete"}),
])
def test_unknown_items_are_404(client, suffix, body):
    assert client.patch("/releases/rel-100" + suffix, json=body).status_code == 404
    assert client.patch("/releases/missing" + suffix, json=body).status_code == 404


@pytest.mark.parametrize("path,body", [
    ("/tests/TEST-001", {"status": "unknown"}),
    ("/tests/TEST-001", {"status": "passed", "critical": False}),
    ("/bugs/BUG-001", {}), ("/bugs/BUG-001", {"severity": "urgent"}),
    ("/security/checks/SEC-001", {"status": "passed", "details": ""}),
    ("/security/findings/FIND-001", {}),
    ("/documents/DOC-001", {"status": "approved"}),
    ("/documents/DOC-001", {"status": "complete", "required": False}),
    ("/release-notes", {}), ("/release-notes", {"content": " "}),
    ("/deployment-check", {"environment": "live"}),
    ("/deployment-check", {"environment": "production", "ready": True}),
])
def test_invalid_updates_cannot_override_policy(client, path, body):
    if path == "/deployment-check":
        response = client.post("/releases/rel-100" + path, json=body)
    else:
        response = client.patch("/releases/rel-100" + path, json=body)
    assert response.status_code == 422


@pytest.mark.parametrize("path,params", [
    ("/tests", {"status": "unknown"}), ("/tests", {"module": "other"}),
    ("/tests", {"critical": "maybe"}), ("/bugs", {"severity": "urgent"}),
    ("/security/checks", {"status": "skipped"}), ("/security/findings", {"status": "closed"}),
    ("/documents", {"status": "published"}),
])
def test_invalid_filters_are_422(client, path, params):
    assert client.get("/releases/rel-100" + path, params=params).status_code == 422


def test_policies_are_read_only_and_check_does_not_mutate(client):
    assert client.get("/deployment-rules/staging").json()["min_test_pass_rate"] == 80
    assert client.get("/deployment-rules/production").json()["min_test_pass_rate"] == 100
    assert client.get("/deployment-rules/other").status_code == 422
    assert client.patch("/deployment-rules/production", json={"min_test_pass_rate": 0}).status_code == 405
    before = client.app.state.store.get_release("rel-100").model_dump()
    readiness(client)
    readiness(client)
    assert client.app.state.store.get_release("rel-100").model_dump() == before
    assert client.post("/releases/missing/reset").status_code == 404
    assert client.post("/releases/missing/deployment-check", json={"environment": "staging"}).status_code == 404
