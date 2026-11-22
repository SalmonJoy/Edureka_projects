from agent.config import load_settings


def test_explicit_file_wins_without_exposing_credentials(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text('OPEN_ROUTER_API_KEY="file-key"\nQDRANT_API_KEY="memory-key"\nQDRANT_ENDPOINT="http://localhost:6333"\n')
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "stale-key")
    settings = load_settings(path)
    assert settings.openrouter_api_key == "file-key"
    assert "file-key" not in repr(settings)
    assert "memory-key" not in repr(settings)
    assert settings.collection == "Edureka"
    assert settings.embedding_model == "nomic-embed-text:latest"
