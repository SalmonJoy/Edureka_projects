"""Three deliberately different fictional releases. No CI or scanning is performed."""

from backend.models import (
    Bug, Document, Release, ReleaseEvidence, ReleaseNotes, SecurityCheck,
    SecurityFinding, TestResult,
)

# Shared cases make it easy to compare the same evidence across release candidates.
TEST_CASES = [
    ("TEST-001", "Catalog search returns matching products", "catalog", "unit", False),
    ("TEST-002", "Cart retains selected quantities", "cart", "unit", True),
    ("TEST-003", "Checkout calculates the order total", "checkout", "e2e", True),
    ("TEST-004", "Payment capture creates one charge", "payments", "integration", True),
    ("TEST-005", "Inventory reservation prevents overselling", "inventory", "integration", True),
    ("TEST-006", "Order creation saves the receipt", "orders", "e2e", True),
    ("TEST-007", "Refund updates the payment record", "payments", "integration", False),
    ("TEST-008", "Coupon applies to eligible products", "checkout", "unit", False),
    ("TEST-009", "Order notification contains the receipt", "orders", "integration", False),
    ("TEST-010", "Catalog sorting respects the selected order", "catalog", "unit", False),
]

BUG_CASES = [
    ("BUG-001", "Duplicate charge on checkout retry", "A simulated retry can submit the same charge twice.", "payments", "critical"),
    ("BUG-002", "Stock count lags after reservation", "A simulated inventory race exposes stale stock counts.", "inventory", "high"),
    ("BUG-003", "Coupon label is inconsistent", "The displayed coupon label differs between cart and checkout.", "checkout", "medium"),
    ("BUG-004", "Receipt spacing needs adjustment", "A cosmetic spacing issue affects the order receipt.", "orders", "low"),
]

CHECK_CASES = [
    ("SEC-001", "Static code analysis"),
    ("SEC-002", "Dependency scan"),
    ("SEC-003", "Secret scan"),
    ("SEC-004", "Configuration review"),
]

FINDING_CASES = [
    ("FIND-001", "SEC-002", "Critical dependency finding", "Fictional package risk requiring a patched dependency.", "critical"),
    ("FIND-002", "SEC-004", "Overly broad service permissions", "A fictional service role has excessive permissions.", "high"),
    ("FIND-003", "SEC-001", "Verbose error information", "A simulated error handler exposes unnecessary internal details.", "medium"),
]

DOCUMENT_CASES = [
    ("DOC-001", "User guide", "Shopping, checkout, refunds, and customer-facing changes."),
    ("DOC-002", "API documentation", "Request/response examples for changed Shopflow endpoints."),
    ("DOC-003", "Deployment runbook", "Preparation, rollout steps, and post-release verification."),
    ("DOC-004", "Rollback plan", "Backout steps and verification of the previous version."),
]


def make_seed_release(release_id: str, version: str, scenario: str) -> ReleaseEvidence:
    """Build independent model objects, then apply small scenario-specific differences."""
    tests = []
    for index, (item_id, name, module, suite, critical) in enumerate(TEST_CASES):
        status = "passed"
        if scenario == "needs_attention" and index >= 8:
            status = "skipped" if index == 8 else "failed"
        elif scenario == "blocked" and index >= 4:
            status = "failed" if index < 8 else "skipped"
        tests.append(TestResult.model_validate({
            "id": item_id, "release_id": release_id, "name": name, "module": module,
            "suite": suite, "critical": critical, "status": status,
            "duration_ms": 0 if status == "skipped" else 40 + index * 17,
            "failure_reason": f"Simulated assertion failure: {name.lower()}." if status == "failed" else None,
        }))

    bug_states = {
        "ready": ["resolved", "resolved", "resolved", "open"],
        "needs_attention": ["resolved", "open", "in_progress", "resolved"],
        "blocked": ["open", "open", "in_progress", "open"],
    }[scenario]
    bugs = [Bug.model_validate({
        "id": item_id, "release_id": release_id, "title": title,
        "description": description, "module": module, "severity": severity, "status": status,
    }) for (item_id, title, description, module, severity), status in zip(BUG_CASES, bug_states)]

    checks = []
    for item_id, name in CHECK_CASES:
        status = "passed"
        if scenario == "blocked" and item_id == "SEC-002":
            status = "failed"
        elif scenario == "blocked" and item_id == "SEC-003":
            status = "not_run"
        details = {
            "passed": "Simulated check execution completed; review findings separately.",
            "failed": "Simulated scanner execution failed; evidence is incomplete.",
            "not_run": "This simulated scan has not been executed.",
        }[status]
        checks.append(SecurityCheck.model_validate({
            "id": item_id, "release_id": release_id, "name": name,
            "required": True, "status": status, "details": details,
        }))

    findings = []
    for index, (item_id, check_id, title, description, severity) in enumerate(FINDING_CASES):
        status = "open" if scenario == "blocked" or (scenario == "needs_attention" and index == 2) else "resolved"
        findings.append(SecurityFinding.model_validate({
            "id": item_id, "release_id": release_id, "check_id": check_id,
            "title": title, "description": description, "severity": severity, "status": status,
        }))

    document_states = {
        "ready": ["complete"] * 4,
        "needs_attention": ["complete", "in_review", "complete", "draft"],
        "blocked": ["draft", "in_review", "draft", "draft"],
    }[scenario]
    documents = [Document.model_validate({
        "id": item_id, "release_id": release_id, "title": title,
        "description": description, "required": True, "status": status,
    }) for (item_id, title, description), status in zip(DOCUMENT_CASES, document_states)]

    release = Release.model_validate({
        "id": release_id, "version": version, "title": f"Shopflow {version}",
        "description": "Fictional candidate covering catalog, cart, checkout, payments, inventory, and orders.",
        "initial_scenario": scenario,
    })
    return ReleaseEvidence(
        release=release, tests=tests, bugs=bugs, checks=checks, findings=findings, documents=documents,
        release_notes=ReleaseNotes(
            release_id=release_id, status="approved" if scenario == "ready" else "draft",
            content=f"# Shopflow {version}\n\n- Updated checkout validation.\n- Improved inventory reservation handling.\n- Refined payment and order receipts.\n\nFictional release notes for the learning simulator.",
        ),
    )


SEED_RELEASES = {
    "rel-100": make_seed_release("rel-100", "1.0.0", "ready"),
    "rel-110": make_seed_release("rel-110", "1.1.0", "needs_attention"),
    "rel-120": make_seed_release("rel-120", "1.2.0", "blocked"),
}
