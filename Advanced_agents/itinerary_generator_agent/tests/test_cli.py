from main import main


def test_cli_runs_same_graph_and_saves_tagged_memory(runtime, services, monkeypatch, capsys):
    monkeypatch.setattr("main.create_runtime", lambda: runtime)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert main(["--user-id", "cli-test", "--test-memory"]) == 0
    output = capsys.readouterr().out
    assert "Status: complete" in output
    assert "Qdrant memory: saved" in output
    saved = next(iter(services.memory.saved.values()))
    assert saved["request"]["user_id"] == "cli-test"
    assert saved["is_test"] is True
    assert len(services.tools.checks) == 3


def test_cli_quit_does_not_execute_or_claim_success(runtime, services, monkeypatch, capsys):
    monkeypatch.setattr("main.create_runtime", lambda: runtime)
    monkeypatch.setattr("builtins.input", lambda prompt: "q")
    assert main([]) == 0
    assert "no final memory was saved" in capsys.readouterr().out
    assert services.tools.checks == []
    assert services.memory.save_calls == 0


def test_cli_invalid_inputs_are_readable(capsys):
    assert main(["--nights", "0"]) == 1
    assert "Agent stopped" in capsys.readouterr().out
