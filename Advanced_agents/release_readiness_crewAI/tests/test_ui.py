"""UI tests keep model calls behind submit and incomplete reviews out of exports."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from release_crew.models import ReviewRequest, ReviewResult
from release_crew.runner import run_review

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


@pytest.fixture
def app(monkeypatch, client, settings):
    monkeypatch.setattr("release_crew.client.ShopflowClient", lambda url: client)
    monkeypatch.setattr("release_crew.config.load_settings", lambda: settings)
    return AppTest.from_file(str(APP), default_timeout=10)


@pytest.mark.parametrize("release_id,environment,alert,text", [
    ("rel-100", "production", "success", "GO"),
    ("rel-110", "staging", "warning", "GO WITH WARNINGS"),
    ("rel-110", "production", "error", "NO GO"),
])
def test_submit_and_reruns_keep_one_report(app, monkeypatch, client, settings, fake_factory, release_id, environment, alert, text):
    def review(request, configured):
        return run_review(request, configured, client=client, crew_factory=fake_factory)

    mocked = Mock(side_effect=review)
    monkeypatch.setattr("release_crew.runner.run_review", mocked)
    app.run()
    assert not app.exception
    mocked.assert_not_called()
    app.selectbox(key="release_id").select(release_id)
    app.segmented_control(key="environment").set_value(environment)
    app.button(key="run_review").click().run()
    assert not app.exception
    mocked.assert_called_once()
    assert text in getattr(app, alert)[0].value
    assert len(app.get("download_button")) == 2
    first = app.session_state["review_result"].report.run_id
    app.button(key="refresh_releases").click().run()
    assert not app.exception
    mocked.assert_called_once()
    assert app.session_state["review_result"].report.run_id == first
    assert len(app.get("download_button")) == 2


def test_failed_new_review_clears_previous_go(app, monkeypatch, client, settings, fake_factory):
    approved = run_review(ReviewRequest(release_id="rel-100"), settings, client=client, crew_factory=fake_factory)
    mocked = Mock(side_effect=[approved, ReviewResult(status="incomplete", error="Provider unavailable")])
    monkeypatch.setattr("release_crew.runner.run_review", mocked)
    app.run()
    app.button(key="run_review").click().run()
    assert len(app.success) == 1 and len(app.get("download_button")) == 2
    app.button(key="run_review").click().run()
    assert not app.exception
    assert "INCOMPLETE" in app.error[0].value
    assert not app.success and not app.get("download_button")
    assert mocked.call_count == 2


def test_api_offline_does_not_call_model(app, api, monkeypatch):
    api.offline = True
    mocked = Mock()
    monkeypatch.setattr("release_crew.runner.run_review", mocked)
    app.run()
    assert not app.exception and app.error
    assert not app.get("download_button")
    mocked.assert_not_called()
