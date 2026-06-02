from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.config import paths
from forge.workspace import load_workspace_context


@pytest.mark.parametrize("depth", [1, 2])
def test_load_workspace_context_from_ancestor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    depth: int,
) -> None:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    nested = root
    for i in range(depth):
        nested = nested / f"d{i}"
    nested.mkdir(parents=True)

    dot = paths.workspace_dotdir(root)
    (dot / "ASSISTANT.md").write_text("workspace rules", encoding="utf-8")
    (dot / "workspace.json").write_text(
        json.dumps({"name": "repo", "language": "python"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (dot / "settings.json").write_text(
        json.dumps({"shell_timeout_seconds": 120, "feature_flag": "base"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (dot / "settings.local.json").write_text(
        json.dumps({"shell_timeout_seconds": 30}, ensure_ascii=False),
        encoding="utf-8",
    )

    ctx = load_workspace_context(nested)
    assert ctx.mode == "workspace"
    assert ctx.root_path == root.resolve()
    assert "workspace rules" in ctx.assistant_prompt
    assert ctx.settings["name"] == "repo"
    assert ctx.settings["feature_flag"] == "base"
    assert ctx.settings["shell_timeout_seconds"] == 30


def test_load_workspace_context_falls_back_to_global(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    workdir = tmp_path / "no_repo"
    workdir.mkdir()
    paths.global_workspace_assistant_path().write_text("global rules", encoding="utf-8")
    paths.global_workspace_config_path().write_text(
        json.dumps({"name": "global"}, ensure_ascii=False),
        encoding="utf-8",
    )
    paths.global_workspace_settings_path().write_text(
        json.dumps({"shell_timeout_seconds": 200}, ensure_ascii=False),
        encoding="utf-8",
    )
    paths.global_workspace_local_settings_path().write_text(
        json.dumps({"shell_timeout_seconds": 40, "feature_flag": "local"}, ensure_ascii=False),
        encoding="utf-8",
    )

    ctx = load_workspace_context(workdir)
    assert ctx.mode == "global"
    assert ctx.root_path == paths.global_workspace_dir()
    assert ctx.assistant_prompt == "global rules"
    assert ctx.settings["name"] == "global"
    assert ctx.settings["shell_timeout_seconds"] == 40
    assert ctx.settings["feature_flag"] == "local"
