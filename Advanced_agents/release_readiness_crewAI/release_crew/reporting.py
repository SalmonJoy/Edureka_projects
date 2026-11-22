"""Render the same validated report as Markdown or JSON, without more LLM calls."""

from release_crew.models import ReadinessReport


def cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def report_markdown(report: ReadinessReport) -> str:
    manager = report.manager
    lines = [
        f"# Shopflow {report.release.version} release-readiness report",
        "", f"**Recommendation:** {manager.recommendation}",
        f"**Release:** {report.release.id} · **Environment:** {report.environment}",
        f"**Reviewed at (UTC):** {report.completed_at}", f"**Run ID:** {report.run_id}",
        "", "## Executive summary", "", manager.executive_summary,
        "", "## Deployment gates", "",
        "| Gate | Result | Expected | Actual | Reason |", "|---|---|---|---|---|",
    ]
    for gate in report.deployment_check.gates:
        lines.append("| " + " | ".join(cell(value) for value in (gate.name, "PASS" if gate.passed else "FAIL", gate.expected, gate.actual, gate.reason)) + " |")
    for title, findings in [("Blockers", manager.blockers), ("Advisory risks", manager.advisory_risks)]:
        lines.extend(["", f"## {title}", ""])
        if not findings:
            lines.append("None reported.")
        for finding in findings:
            lines.append(f"- {finding.observation} Action: {finding.suggested_action} Sources: {', '.join(finding.evidence_refs)}.")
    if report.deployment_check.warnings:
        lines.extend(["", "## Policy warnings", ""])
        lines.extend(f"- {warning}" for warning in report.deployment_check.warnings)
    lines.extend(["", "## Specialist assessments", ""])
    for assessment in report.assessments:
        heading = "QA" if assessment.domain == "qa" else assessment.domain.title()
        lines.extend([f"### {heading}", "", assessment.summary, ""])
        for finding in assessment.findings:
            lines.append(f"- [{finding.impact}] {finding.observation} Sources: {', '.join(finding.evidence_refs)}.")
    lines.extend(["", "## Recommended actions", ""])
    if not manager.actions:
        lines.append("No blocking follow-up actions were reported.")
    for action in manager.actions:
        lines.append(f"- **{action.owner} / {action.priority}:** {action.description} Sources: {', '.join(action.evidence_refs)}.")
    lines.extend(["", "## Evidence sources", ""])
    lines.extend(f"- `{item.method} {item.source}` (captured {item.captured_at})" for item in report.evidence)
    lines.extend(["", "This is an assessment of fictional Shopflow evidence, not a deployment or approval.", ""])
    return "\n".join(lines)
