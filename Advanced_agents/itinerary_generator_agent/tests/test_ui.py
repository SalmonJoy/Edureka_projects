from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"


def labeled(widgets, label):
    return next(widget for widget in widgets if widget.label == label)


@pytest.fixture
def ui(runtime, settings, monkeypatch):
    monkeypatch.setattr("agent.runtime.create_runtime", lambda config=None: runtime)
    monkeypatch.setattr("agent.config.load_settings", lambda: settings)
    return AppTest.from_file(str(APP), default_timeout=15).run()


def test_ui_pause_replan_and_visible_completion(ui, services):
    assert not ui.exception
    labeled(ui.button, "Generate initial plan").click().run()
    assert not ui.exception
    assert any("Run status: paused" in item.value for item in ui.subheader)
    assert [tab.label for tab in ui.tabs] == ["Itinerary", "Execution trace", "Reflection", "Qdrant memory"]
    services.tools.data.hotels["jaipur-haveli"].available = False
    labeled(ui.button, "Continue next step").click().run()
    assert not ui.exception
    assert ui.session_state["run_view"]["plan"]["hotel_id"] == "jaipur-market"
    assert ui.session_state["run_view"]["replan_count"] == 1
    for _ in range(3):
        labeled(ui.button, "Continue next step").click().run()
    assert not ui.exception
    assert any("Run status: complete" in item.value for item in ui.subheader)
    assert any("saved to Qdrant" in message.value for message in ui.success)
    assert services.memory.save_calls == 1


def test_ui_memory_failure_is_visible(ui, services):
    services.memory.fail_read = True
    labeled(ui.button, "Generate initial plan").click().run()
    assert not ui.exception
    assert any("Memory retrieval failed" in item.value for item in ui.error)
    assert not any(button.label == "Continue next step" for button in ui.button)


def test_ui_shows_memory_write_failure_without_losing_itinerary(ui, services):
    services.memory.fail_save = True
    labeled(ui.button, "Generate initial plan").click().run()
    for _ in range(3):
        labeled(ui.button, "Continue next step").click().run()
    assert not ui.exception
    assert any("Run status: complete" in item.value for item in ui.subheader)
    assert any("not confirmed" in item.value for item in ui.warning)


def test_ui_backend_offline_message(ui, services):
    services.tools.fail = True
    ui.run()
    assert not ui.exception
    assert any("Travel service offline" in item.value for item in ui.error)
