from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def labeled(widgets, label):
    return next(widget for widget in widgets if widget.label == label)


@pytest.fixture
def ui(client, monkeypatch):
    def request(method, url, *, params=None, json=None, timeout=None):
        from urllib.parse import urlsplit

        api_response = client.request(method, urlsplit(url).path, params=params, json=json)
        result = requests.Response()
        result.status_code = api_response.status_code
        result._content = api_response.content
        return result

    monkeypatch.setattr(requests, "request", request)
    return AppTest.from_file(str(APP), default_timeout=15).run()


def test_all_screens_and_environment_comparison(ui):
    assert not ui.exception
    for section in ("Tests", "Bugs", "Security", "Documentation", "Deployment", "Overview"):
        ui.selectbox(key="section").select(section).run()
        assert not ui.exception
    ui.selectbox(key="release_id").select("rel-110").run()
    ui.selectbox(key="section").select("Deployment").run()
    assert any("Not ready for production" in message.value for message in ui.error)
    ui.segmented_control(key="environment").set_value("staging").run()
    assert not ui.exception
    assert any("Ready for staging" in message.value for message in ui.success)
    assert len(ui.warning) == 2


def test_ui_filter_and_test_update(ui, client):
    ui.selectbox(key="release_id").select("rel-110").run()
    ui.selectbox(key="section").select("Tests").run()
    labeled(ui.selectbox, "Test outcome filter").select("failed")
    ui.button(key="filter_tests:rel-110").click().run()
    assert not ui.exception
    assert len(ui.dataframe[0].value) == 1
    assert ui.dataframe[0].value.iloc[0]["id"] == "TEST-010"
    ui.selectbox(key="test_item:rel-110").select("TEST-010").run()
    labeled(ui.selectbox, "Test outcome").select("passed")
    ui.button(key="save_test:rel-110").click().run()
    assert not ui.exception
    assert client.get("/releases/rel-110/tests/summary").json()["passed"] == 9
    assert any("Test result updated" in message.value for message in ui.success)
    assert labeled(ui.metric, "Failed").value == "0"


def test_ui_invalid_test_update_is_visible(ui, client):
    ui.selectbox(key="section").select("Tests").run()
    labeled(ui.selectbox, "Test outcome").select("failed")
    ui.button(key="save_test:rel-100").click().run()
    assert not ui.exception
    assert any("failure_reason" in message.value for message in ui.error)
    assert client.get("/releases/rel-100/tests/summary").json()["passed"] == 10


def test_ui_bug_and_security_editors(ui, client):
    ui.selectbox(key="section").select("Bugs").run()
    labeled(ui.selectbox, "Bug status").select("open")
    ui.button(key="save_bug:rel-100").click().run()
    assert not ui.exception
    assert client.get("/releases/rel-100/bugs", params={"status": "open", "severity": "critical"}).json()

    ui.selectbox(key="section").select("Security").run()
    labeled(ui.selectbox, "Security check status").select("failed")
    labeled(ui.text_area, "Security check details").set_value("Simulated execution failure")
    ui.button(key="save_check:rel-100").click().run()
    assert not ui.exception
    labeled(ui.selectbox, "Finding status").select("open")
    ui.button(key="save_finding:rel-100").click().run()
    assert not ui.exception
    report = client.get("/releases/rel-100/security/report").json()
    assert report["failed_checks"] == 1 and report["unresolved_findings"] == 1


def test_ui_documents_notes_and_release_scoped_reset(ui, client):
    ui.selectbox(key="section").select("Documentation").run()
    labeled(ui.selectbox, "Document status").select("draft")
    ui.button(key="save_document:rel-100").click().run()
    assert not ui.exception
    labeled(ui.selectbox, "Release notes status").select("draft")
    labeled(ui.text_area, "Release notes content").set_value("Edited sample notes")
    ui.button(key="save_notes:rel-100").click().run()
    assert not ui.exception
    assert client.get("/releases/rel-100/release-notes").json()["content"] == "Edited sample notes"
    client.patch("/releases/rel-110/bugs/BUG-002", json={"status": "resolved"})
    ui.button(key="reset_release").click().run()
    assert not ui.exception
    assert client.get("/releases/rel-100/release-notes").json()["status"] == "approved"
    assert client.get("/releases/rel-100/documents").json()[0]["status"] == "complete"
    assert client.get("/releases/rel-110/bugs", params={"severity": "high", "unresolved_only": True}).json() == []


def test_ui_backend_failure_is_readable(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("Offline")

    monkeypatch.setattr(requests, "request", offline)
    ui = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not ui.exception
    assert any("Cannot reach the Shopflow API" in message.value for message in ui.error)
