from __future__ import annotations

import json
from pathlib import Path

from forge.cli.main import main


def test_cli_init_creates_workspace_files(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(["init"])
    assert code == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    dot = tmp_path / ".assistant"
    assert dot.is_dir()
    assert (dot / "workspace.json").exists()
    assert (dot / "settings.json").exists()
    assert (dot / "settings.local.json").exists()
    assert (dot / "ASSISTANT.md").exists()
    assert len(data["created"]) == 4
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").strip() == (
        ".assistant/settings.local.json"
    )


def test_cli_init_is_idempotent(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    _ = capsys.readouterr()
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["created"] == []
    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines.count(".assistant/settings.local.json") == 1
