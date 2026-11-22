"""UI entry point: uv run streamlit run streamlit_app.py --server.port 8504"""

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from release_crew.client import ShopflowClient
from release_crew.config import load_settings
from release_crew.errors import ReviewError
from release_crew.models import ReviewRequest
from release_crew.reporting import report_markdown
from release_crew.runner import run_review

st.set_page_config(page_title="Shopflow release crew", page_icon=":material/groups:", layout="wide")
st.title("Shopflow release-readiness crew")
st.write("A release manager delegates to QA, security, and documentation specialists, then consolidates their evidence.")
st.caption("Assessment only. Make simulated fixes in the Shopflow simulator, then run a new review.")

settings = load_settings()
with st.sidebar:
    base_url = st.text_input("Shopflow API URL", value=settings.shopflow_url, key="api_url")
    st.caption(f"Model: {settings.model}")
    st.button("Refresh releases", key="refresh_releases", icon=":material/refresh:")
    st.link_button("Open Shopflow simulator", "http://localhost:8503")

client = ShopflowClient(base_url)
try:
    releases = client.list_releases()
except ReviewError as error:
    st.error(str(error))
    st.info("Start the Shopflow API from its project folder: uv run uvicorn backend.main:app --reload --port 8001")
    st.stop()
finally:
    client.close()

if not releases:
    st.info("Shopflow has no release candidates to review.")
    st.stop()

by_id = {release.id: release for release in releases}
with st.form("review_inputs", border=True):
    release_id = st.selectbox("Release candidate", list(by_id), format_func=lambda value: f"{by_id[value].version} — {by_id[value].initial_scenario.replace('_', ' ')} seed", key="release_id")
    environment = st.segmented_control("Environment", ["staging", "production"], default="production", required=True, key="environment")
    submitted = st.form_submit_button("Run readiness review", key="run_review", icon=":material/play_arrow:")

with st.expander("The four roles"):
    st.dataframe([
        {"Agent": "Release manager", "Responsibility": "Delegate, consolidate, and recommend"},
        {"Agent": "QA engineer", "Responsibility": "Tests and bug criticality"},
        {"Agent": "Security analyst", "Responsibility": "Vulnerabilities and scan evidence"},
        {"Agent": "Documentation specialist", "Responsibility": "Documents and release notes"},
    ], hide_index=True, alt="Four agents and their release-review responsibilities")

if submitted:
    # Read the key again on submission, so a rotated .env key takes effect immediately.
    settings = replace(load_settings(), shopflow_url=base_url.strip().rstrip("/"))
    st.session_state.pop("review_result", None)
    request = ReviewRequest.model_validate({"release_id": release_id, "environment": environment})
    with st.status("Running manager-led review…", expanded=True) as status:
        st.write("The manager supervises QA, security, and documentation reviews before creating the final report.")
        result = run_review(request, settings)
        st.session_state["review_result"] = result
        status.update(label="Review complete" if result.status == "complete" else "Review incomplete", state="complete" if result.status == "complete" else "error", expanded=False)

result = st.session_state.get("review_result")
if result is None:
    st.info("Select a release and environment, then run a review. Model calls start only when you submit.")
    st.stop()

if result.status == "incomplete":
    st.error("INCOMPLETE: " + (result.error or "The crew did not produce a validated report."))
    for assessment in result.assessments:
        with st.expander(f"Completed partial assessment: {assessment.domain}"):
            st.json(assessment.model_dump())
    if result.activity:
        st.dataframe([event.model_dump() for event in result.activity], hide_index=True, alt="Activity for the incomplete review")
    st.stop()

report = result.report
assert report is not None
reviewed_at = datetime.fromisoformat(report.completed_at).astimezone(ZoneInfo("Asia/Kolkata"))
st.subheader(f"Reviewed Shopflow {report.release.version} — {report.environment}")
st.caption(f"Completed {reviewed_at:%d %b %Y, %H:%M} IST · Run {report.run_id} · Evidence was rechecked before publication.")
if result.recommendation == "NO_GO":
    st.error("NO GO — one or more deployment gates failed.")
elif result.recommendation == "GO_WITH_WARNINGS":
    st.warning("GO WITH WARNINGS — deployment gates pass with permitted policy gaps.")
else:
    st.success("GO — all deployment gates passed without policy warnings.")

final_tab, specialists_tab, evidence_tab, activity_tab = st.tabs(["Manager report", "Specialist assessments", "Evidence and gates", "Activity"])
with final_tab:
    st.markdown(report_markdown(report))
    with st.container(horizontal=True):
        file_stem = f"{report.release.id}-{report.environment}-{report.run_id}"
        st.download_button("Download Markdown", report_markdown(report), file_name=file_stem + ".md", mime="text/markdown", on_click="ignore", key="download_markdown")
        st.download_button("Download JSON", report.model_dump_json(indent=2), file_name=file_stem + ".json", mime="application/json", on_click="ignore", key="download_json")
with specialists_tab:
    for assessment in report.assessments:
        with st.container(border=True):
            st.subheader("QA engineer" if assessment.domain == "qa" else assessment.domain.title())
            st.write(assessment.summary)
            if assessment.findings:
                st.dataframe([finding.model_dump() for finding in assessment.findings], hide_index=True, alt=f"{assessment.domain} findings and source references")
            st.caption("Sources: " + ", ".join(assessment.evidence_refs))
with evidence_tab:
    st.dataframe([gate.model_dump() for gate in report.deployment_check.gates], hide_index=True, alt="Authoritative Shopflow deployment gate results")
    with st.expander("Selected environment policy"):
        st.json(report.policy)
    for record in report.evidence:
        with st.expander(f"{record.method} {record.source}"):
            st.caption("Captured " + record.captured_at)
            st.json(record.response)
with activity_tab:
    st.caption("Delegations, evidence reads, and validated outputs. Private model reasoning is not recorded.")
    st.dataframe([event.model_dump() for event in report.activity], hide_index=True, alt="Manager delegation and specialist tool activity for this run")
