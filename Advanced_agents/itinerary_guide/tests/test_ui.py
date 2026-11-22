"""Run real Streamlit widgets against the API in-process, without network ports."""

from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


def widget_with_label(widgets, label):
    return next(widget for widget in widgets if widget.label == label)


@pytest.fixture
def ui(client, monkeypatch):
    def request(method, url, *, params=None, json=None, timeout=None):
        # Keep the actual UI HTTP client and API routes in the test path.
        from urllib.parse import urlsplit

        api_response = client.request(method, urlsplit(url).path, params=params, json=json)
        response = requests.Response()
        response.status_code = api_response.status_code
        response._content = api_response.content
        return response

    monkeypatch.setattr(requests, "request", request)
    return AppTest.from_file(str(APP_PATH), default_timeout=15).run()


def test_ui_catalog_search_and_budget(ui):
    assert not ui.exception
    assert [tab.label for tab in ui.tabs] == ["Travel Catalog", "Availability Simulator", "Budget Calculator"]
    widget_with_label(ui.text_input, "Search destinations").set_value("nothing-like-this")
    widget_with_label(ui.button, "Search catalog").click().run()
    assert not ui.exception
    assert any("No destinations match" in message.value for message in ui.info)

    ui.multiselect(key="budget_activities").select("goa-walk")
    widget_with_label(ui.number_input, "Rooms").set_value(2)
    widget_with_label(ui.number_input, "Travelers").set_value(3)
    widget_with_label(ui.number_input, "Food total (INR)").set_value(1000)
    widget_with_label(ui.number_input, "Transport total (INR)").set_value(500)
    widget_with_label(ui.number_input, "Miscellaneous total (INR)").set_value(200)
    widget_with_label(ui.button, "Calculate budget").click().run()
    assert not ui.exception
    assert widget_with_label(ui.metric, "Total trip cost").value == "₹15,200.00"

    ui.selectbox(key="budget_destination").select("jaipur").run()
    assert not ui.exception
    assert ui.multiselect(key="budget_activities").value == []
    assert not ui.metric
    assert ui.selectbox(key="budget_hotel").value.startswith("jaipur-")


def test_ui_expanded_catalog_and_new_destination_budget(ui):
    assert not ui.exception
    assert len(ui.selectbox(key="budget_destination").options) == 20
    ui.selectbox(key="budget_destination").select("munnar").run()
    assert not ui.exception
    assert ui.selectbox(key="budget_hotel").value == "munnar-tea"
    ui.multiselect(key="budget_activities").select("munnar-tea-walk")
    widget_with_label(ui.button, "Calculate budget").click().run()
    assert not ui.exception
    assert widget_with_label(ui.metric, "Total trip cost").value == "₹7,600.00"


def test_ui_toggle_reset_and_no_hotels(ui, client):
    widget_with_label(ui.button, "Make unavailable").click().run()
    assert not ui.exception
    assert any("No hotels are available" in message.value for message in ui.warning)
    assert client.get("/hotels", params={"q": "Palm"}).json()[0]["available"] is False

    widget_with_label(ui.button, "Reset all sample availability").click().run()
    assert not ui.exception
    assert ui.selectbox(key="budget_hotel").value == "goa-beach"

    widget_with_label(ui.radio, "Item type").set_value("Activity").run()
    widget_with_label(ui.button, "Make unavailable").click().run()
    assert not ui.exception
    assert not any("Coastal Walking Tour" in option for option in ui.multiselect(key="budget_activities").options)
    assert client.get("/activities", params={"q": "Coastal Walking"}).json()[0]["available"] is False


def test_ui_shows_backend_connection_failure(monkeypatch):
    def offline(*args, **kwargs):
        raise requests.ConnectionError("Offline")

    monkeypatch.setattr(requests, "request", offline)
    ui = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
    assert not ui.exception
    assert any("Cannot reach the travel API" in error.value for error in ui.error)


def test_ui_handles_availability_change_before_calculation(ui, client):
    client.patch("/hotels/goa-beach/availability", json={"available": False})
    # The UI fetches fresh availability before building the budget form.
    ui.run()
    assert not ui.exception
    assert any("No hotels are available" in message.value for message in ui.warning)
