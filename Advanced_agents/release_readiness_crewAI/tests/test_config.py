from release_crew.config import load_settings


def test_rotated_file_key_wins_over_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "stale-test-value")
    env_file = tmp_path / ".env"
    env_file.write_text("OPEN_ROUTER_API_KEY=fresh-test-value\nREADINESS_MODEL=openai/gpt-oss-20b\n")
    first = load_settings(env_file)
    assert first.openrouter_api_key == "fresh-test-value"
    assert "fresh-test-value" not in repr(first)
    env_file.write_text("OPEN_ROUTER_API_KEY=rotated-test-value\n")
    assert load_settings(env_file).openrouter_api_key == "rotated-test-value"


def test_missing_credentials_still_allows_ui_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert load_settings(tmp_path / "missing.env").openrouter_api_key == ""
