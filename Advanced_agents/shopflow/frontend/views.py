"""Small feature screens. Every edit, summary, and gate calculation uses HTTP."""

import streamlit as st

from frontend.api_client import APIClient, APIError

SEVERITIES = ["critical", "high", "medium", "low"]


def table(rows: list[dict], description: str) -> None:
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch", alt=description)
    else:
        st.info("No records match these filters.")


def updated(message: str) -> None:
    # A new token resets editor defaults to the newly returned backend state.
    st.session_state["refresh_token"] += 1
    st.session_state["notice"] = message
    st.rerun()


def save(api: APIClient, path: str, payload: dict, message: str) -> None:
    try:
        api.patch(path, payload)
    except APIError as error:
        st.error(str(error))
    else:
        updated(message)


def overview(api: APIClient, prefix: str) -> None:
    data = api.get(prefix + "/summary")
    st.header("Release overview")
    st.caption("Computed from current evidence. The initial scenario label describes the seed data, not a permanent readiness decision.")
    with st.container(horizontal=True):
        st.metric("Test pass rate", f"{data['tests']['pass_rate']:g}%", border=True)
        st.metric("Unresolved bugs", data["bugs"]["unresolved"], border=True)
        st.metric("Open security findings", data["security"]["unresolved_findings"], border=True)
        st.metric("Required docs complete", f"{data['documentation']['required_complete']}/{data['documentation']['required_total']}", border=True)
    table([
        {"Environment": environment, "Ready": ready}
        for environment, ready in data["readiness"].items()
    ], "Current staging and production readiness")
    st.write(f"Release notes: **{data['release_notes_status']}**")
    with st.expander("Summary JSON for future agent tools"):
        st.json(data)


def tests(api: APIClient, prefix: str, scope: str) -> None:
    st.header("Test results")
    summary = api.get(prefix + "/tests/summary")
    with st.container(horizontal=True):
        for label, key in [("Passed", "passed"), ("Failed", "failed"), ("Skipped", "skipped")]:
            st.metric(label, summary[key], border=True)
    st.caption(f"Pass rate: {summary['pass_rate']:g}%. Skipped tests remain in the denominator.")
    all_items = api.get(prefix + "/tests")
    with st.form(f"test_filters:{scope}"):
        status = st.selectbox("Test outcome filter", ["all", "passed", "failed", "skipped"], key=f"test_filter_status:{scope}")
        module = st.selectbox("Test module filter", ["all", *sorted({item['module'] for item in all_items})], key=f"test_filter_module:{scope}")
        critical = st.selectbox("Test criticality filter", ["all", "critical", "noncritical"], key=f"test_filter_critical:{scope}")
        st.form_submit_button("Filter tests", key=f"filter_tests:{scope}")
    matches = api.get(prefix + "/tests", status=None if status == "all" else status,
                      module=None if module == "all" else module,
                      critical=None if critical == "all" else critical == "critical")
    table(matches, "Filtered passed, failed, and skipped test results")
    if not all_items:
        return
    by_id = {item["id"]: item for item in all_items}
    item_id = st.selectbox("Test to edit", list(by_id), format_func=lambda value: f"{value} — {by_id[value]['name']}", key=f"test_item:{scope}")
    item = by_id[item_id]
    with st.form(f"test_editor:{scope}:{item_id}:{st.session_state['refresh_token']}"):
        choices = ["passed", "failed", "skipped"]
        new_status = st.selectbox("Test outcome", choices, index=choices.index(item["status"]), key=f"test_status:{scope}:{item_id}:{st.session_state['refresh_token']}")
        reason = st.text_area("Failure reason (required for failed tests)", value=item["failure_reason"] or "", key=f"test_reason:{scope}:{item_id}:{st.session_state['refresh_token']}")
        submitted = st.form_submit_button("Save test result", key=f"save_test:{scope}")
    if submitted:
        save(api, prefix + f"/tests/{item_id}", {"status": new_status, "failure_reason": reason or None}, "Test result updated.")


def bugs(api: APIClient, prefix: str, scope: str) -> None:
    st.header("Bug tracker")
    all_items = api.get(prefix + "/bugs")
    with st.form(f"bug_filters:{scope}"):
        status = st.selectbox("Bug status filter", ["all", "open", "in_progress", "resolved"], key=f"bug_filter_status:{scope}")
        severity = st.selectbox("Bug severity filter", ["all", *SEVERITIES], key=f"bug_filter_severity:{scope}")
        module = st.selectbox("Bug module filter", ["all", *sorted({item['module'] for item in all_items})], key=f"bug_filter_module:{scope}")
        unresolved = st.checkbox("Unresolved bugs only", key=f"bug_filter_unresolved:{scope}")
        st.form_submit_button("Filter bugs", key=f"filter_bugs:{scope}")
    table(api.get(prefix + "/bugs", status=None if status == "all" else status,
                  severity=None if severity == "all" else severity, module=None if module == "all" else module,
                  unresolved_only=unresolved), "Bugs filtered by status, severity, and module")
    if not all_items:
        return
    by_id = {item["id"]: item for item in all_items}
    item_id = st.selectbox("Bug to edit", list(by_id), format_func=lambda value: f"{value} — {by_id[value]['title']}", key=f"bug_item:{scope}")
    item = by_id[item_id]
    with st.form(f"bug_editor:{scope}:{item_id}:{st.session_state['refresh_token']}"):
        statuses = ["open", "in_progress", "resolved"]
        new_status = st.selectbox("Bug status", statuses, index=statuses.index(item["status"]))
        new_severity = st.selectbox("Bug severity", SEVERITIES, index=SEVERITIES.index(item["severity"]))
        submitted = st.form_submit_button("Save bug", key=f"save_bug:{scope}")
    if submitted:
        save(api, prefix + f"/bugs/{item_id}", {"status": new_status, "severity": new_severity}, "Bug updated.")


def security(api: APIClient, prefix: str, scope: str) -> None:
    st.header("Security evidence")
    st.caption("Check status describes simulated execution. Open findings are evaluated independently; no real scanner runs here.")
    report = api.get(prefix + "/security/report")
    with st.container(horizontal=True):
        st.metric("Required checks passed", f"{report['required_passed']}/{report['required_checks']}", border=True)
        st.metric("Open findings", report["unresolved_findings"], border=True)
    with st.form(f"security_filters:{scope}"):
        check_status = st.selectbox("Security check filter", ["all", "passed", "failed", "not_run"])
        finding_status = st.selectbox("Finding status filter", ["all", "open", "resolved"])
        finding_severity = st.selectbox("Finding severity filter", ["all", *SEVERITIES])
        st.form_submit_button("Filter security evidence", key=f"filter_security:{scope}")
    st.subheader("Checks")
    table(api.get(prefix + "/security/checks", status=None if check_status == "all" else check_status), "Simulated security check outcomes")
    st.subheader("Findings")
    table(api.get(prefix + "/security/findings", status=None if finding_status == "all" else finding_status,
                  severity=None if finding_severity == "all" else finding_severity), "Security findings with severity and resolution status")

    checks = {item["id"]: item for item in report["checks"]}
    if checks:
        check_id = st.selectbox("Security check to edit", list(checks), format_func=lambda value: f"{value} — {checks[value]['name']}", key=f"check_item:{scope}")
        check = checks[check_id]
        with st.form(f"check_editor:{scope}:{check_id}:{st.session_state['refresh_token']}"):
            choices = ["passed", "failed", "not_run"]
            new_status = st.selectbox("Security check status", choices, index=choices.index(check["status"]))
            details = st.text_area("Security check details", value=check["details"])
            submitted = st.form_submit_button("Save security check", key=f"save_check:{scope}")
        if submitted:
            save(api, prefix + f"/security/checks/{check_id}", {"status": new_status, "details": details}, "Security check updated.")

    findings = {item["id"]: item for item in report["findings"]}
    if findings:
        finding_id = st.selectbox("Finding to edit", list(findings), format_func=lambda value: f"{value} — {findings[value]['title']}", key=f"finding_item:{scope}")
        finding = findings[finding_id]
        with st.form(f"finding_editor:{scope}:{finding_id}:{st.session_state['refresh_token']}"):
            statuses = ["open", "resolved"]
            new_status = st.selectbox("Finding status", statuses, index=statuses.index(finding["status"]))
            severity = st.selectbox("Finding severity", SEVERITIES, index=SEVERITIES.index(finding["severity"]))
            submitted = st.form_submit_button("Save finding", key=f"save_finding:{scope}")
        if submitted:
            save(api, prefix + f"/security/findings/{finding_id}", {"status": new_status, "severity": severity}, "Security finding updated.")


def documentation(api: APIClient, prefix: str, scope: str) -> None:
    st.header("Documentation and release notes")
    all_items = api.get(prefix + "/documents")
    with st.form(f"document_filters:{scope}"):
        status = st.selectbox("Document status filter", ["all", "draft", "in_review", "complete"])
        required = st.selectbox("Document requirement filter", ["all", "required", "optional"])
        st.form_submit_button("Filter documents", key=f"filter_documents:{scope}")
    table(api.get(prefix + "/documents", status=None if status == "all" else status,
                  required=None if required == "all" else required == "required"), "Documentation completion and required flags")
    documents = {item["id"]: item for item in all_items}
    if documents:
        document_id = st.selectbox("Document to edit", list(documents), format_func=lambda value: f"{value} — {documents[value]['title']}", key=f"document_item:{scope}")
        item = documents[document_id]
        with st.form(f"document_editor:{scope}:{document_id}:{st.session_state['refresh_token']}"):
            choices = ["draft", "in_review", "complete"]
            new_status = st.selectbox("Document status", choices, index=choices.index(item["status"]))
            submitted = st.form_submit_button("Save document", key=f"save_document:{scope}")
        if submitted:
            save(api, prefix + f"/documents/{document_id}", {"status": new_status}, "Document updated.")

    notes = api.get(prefix + "/release-notes")
    st.subheader("Current release notes")
    st.caption(f"Approval status: {notes['status']}")
    st.markdown(notes["content"])
    with st.form(f"notes_editor:{scope}:{st.session_state['refresh_token']}"):
        choices = ["draft", "in_review", "approved"]
        new_status = st.selectbox("Release notes status", choices, index=choices.index(notes["status"]))
        content = st.text_area("Release notes content", value=notes["content"], height=180)
        submitted = st.form_submit_button("Save release notes", key=f"save_notes:{scope}")
    if submitted:
        save(api, prefix + "/release-notes", {"status": new_status, "content": content}, "Release notes updated.")


def deployment(api: APIClient, prefix: str, environment: str, scope: str) -> None:
    st.header("Deployment rules and readiness")
    st.caption("These are demonstration policies. A readiness check evaluates evidence and never deploys anything.")
    rules = api.get(f"/deployment-rules/{environment}")
    with st.expander("Read-only deployment policy", expanded=True):
        st.json(rules)
    # The endpoint is a pure calculation; every refresh obtains current gates.
    result = api.post(prefix + "/deployment-check", {"environment": environment})
    st.button("Recheck readiness", key=f"recheck:{scope}", icon=":material/refresh:")
    if result["ready"]:
        st.success(f"Ready for {environment}: {result['passed_gates']}/{result['total_gates']} gates passed.")
    else:
        st.error(f"Not ready for {environment}: {result['passed_gates']}/{result['total_gates']} gates passed.")
    for message in result["warnings"]:
        st.warning(message)
    table(result["gates"], "Deployment gate expectations, actual evidence, and pass or fail reasons")
    with st.expander("Readiness JSON for future agent tools"):
        st.json(result)
