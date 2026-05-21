from __future__ import annotations

from types import SimpleNamespace

from forge.cli.main import main


def test_cli_chat_uses_workspace_timeout_default(monkeypatch, tmp_path) -> None:
    captured: dict[str, float] = {}

    monkeypatch.chdir(tmp_path)
    dot = tmp_path / ".assistant"
    dot.mkdir()
    (dot / "settings.json").write_text('{"chat_timeout_seconds": 42}', encoding="utf-8")

    monkeypatch.setattr("forge.cli.main.resolve_token", lambda token: token)

    def _fake_run_chat_once(**kwargs):
        captured["timeout"] = kwargs["timeout_seconds"]
        return SimpleNamespace(
            session_id="sess_1",
            message_id="msg_1",
            finish_reason="stop",
            usage={},
        )

    monkeypatch.setattr("forge.cli.main.run_chat_once", _fake_run_chat_once)
    code = main(["chat", "--base-url", "http://127.0.0.1:8553", "--message", "ping"])
    assert code == 0
    assert captured["timeout"] == 42.0


def test_cli_chat_explicit_timeout_overrides_workspace(monkeypatch, tmp_path) -> None:
    captured: dict[str, float] = {}

    monkeypatch.chdir(tmp_path)
    dot = tmp_path / ".assistant"
    dot.mkdir()
    (dot / "settings.json").write_text('{"chat_timeout_seconds": 42}', encoding="utf-8")

    monkeypatch.setattr("forge.cli.main.resolve_token", lambda token: token)

    def _fake_run_chat_once(**kwargs):
        captured["timeout"] = kwargs["timeout_seconds"]
        return SimpleNamespace(
            session_id="sess_1",
            message_id="msg_1",
            finish_reason="stop",
            usage={},
        )

    monkeypatch.setattr("forge.cli.main.run_chat_once", _fake_run_chat_once)
    code = main(
        [
            "chat",
            "--base-url",
            "http://127.0.0.1:8553",
            "--message",
            "ping",
            "--timeout",
            "5",
        ]
    )
    assert code == 0
    assert captured["timeout"] == 5.0
