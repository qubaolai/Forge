"""CLI chat --template / --auto / --pause-after-phase 透传单测 (S6.5 M1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge.cli.main import main


def _stub_run_chat_once(captured: dict):
    def _fake(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            session_id="sess_1",
            message_id="msg_1",
            finish_reason="stop",
            usage={},
        )

    return _fake


def test_cli_chat_default_no_workflow_kwargs(monkeypatch, tmp_path) -> None:
    """不带 --template / --auto 时, kwargs 中 template_id=None / auto=False."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("forge.cli.main.resolve_token", lambda t: t)
    captured: dict = {}
    monkeypatch.setattr("forge.cli.main.run_chat_once", _stub_run_chat_once(captured))

    code = main(["chat", "--message", "你好"])
    assert code == 0
    assert captured["template_id"] is None
    assert captured["auto"] is False
    assert captured["pause_after_phase"] is False


def test_cli_chat_template_flag_passes_through(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("forge.cli.main.resolve_token", lambda t: t)
    captured: dict = {}
    monkeypatch.setattr("forge.cli.main.run_chat_once", _stub_run_chat_once(captured))

    code = main(["chat", "--message", "修个 bug", "--template", "quick_fix"])
    assert code == 0
    assert captured["template_id"] == "quick_fix"
    assert captured["auto"] is False


def test_cli_chat_auto_flag_passes_through(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("forge.cli.main.resolve_token", lambda t: t)
    captured: dict = {}
    monkeypatch.setattr("forge.cli.main.run_chat_once", _stub_run_chat_once(captured))

    code = main(["chat", "--message", "改点东西", "--auto"])
    assert code == 0
    assert captured["template_id"] is None
    assert captured["auto"] is True


def test_cli_chat_template_and_auto_conflict_errors(monkeypatch, tmp_path, capsys) -> None:
    """--template 与 --auto 互斥, parser.error 退出 2."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("forge.cli.main.resolve_token", lambda t: t)
    monkeypatch.setattr("forge.cli.main.run_chat_once", _stub_run_chat_once({}))

    with pytest.raises(SystemExit) as ex:
        main(["chat", "--message", "x", "--template", "quick_fix", "--auto"])
    assert ex.value.code == 2
    captured = capsys.readouterr()
    assert "不能同时指定" in captured.err
