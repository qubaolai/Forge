from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from forge.core.types.errors import ToolValidationError
from forge.core.types.message import ToolCall
from forge.tools.base import Tool
from forge.tools.executor import ToolExecutor
from forge.workspace.runtime import ToolRuntimePolicy


class _ReadFileTool(Tool):
    name = "read_file"
    description = "read"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"path": args["path"]}


class _ShellTool(Tool):
    name = "shell"
    description = "shell"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout_seconds": {"type": "number"},
        },
        "required": ["command"],
    }

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"timeout_seconds": args.get("timeout_seconds")}


def _make_executor(*tools: Tool, task_workspace_root: str | Path | None = None) -> ToolExecutor:
    fake = type("FakeRegistry", (), {})()
    by_name = {t.name: t for t in tools}
    fake.get = lambda name: by_name.get(name)
    fake.get_all = lambda: list(tools)
    return ToolExecutor(registry=fake, task_workspace_root=task_workspace_root)


def test_read_file_rejects_path_outside_allowlist(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside.txt"
    root.mkdir(parents=True)
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "forge.tools.executor.resolve_tool_runtime_policy",
        lambda start=None: ToolRuntimePolicy(
            shell_timeout_seconds=30.0,
            allowed_roots=(root.resolve(),),
        ),
    )
    exec_ = _make_executor(_ReadFileTool())
    call = ToolCall(id="c1", name="read_file", arguments={"path": str(outside)})
    with pytest.raises(ToolValidationError):
        exec_.execute(call)


def test_read_file_allows_path_within_allowlist(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    inside = root / "a.txt"
    inside.parent.mkdir(parents=True)
    inside.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "forge.tools.executor.resolve_tool_runtime_policy",
        lambda start=None: ToolRuntimePolicy(
            shell_timeout_seconds=30.0,
            allowed_roots=(root.resolve(),),
        ),
    )
    exec_ = _make_executor(_ReadFileTool())
    call = ToolCall(id="c2", name="read_file", arguments={"path": str(inside)})
    msg = exec_.execute(call)
    payload = json.loads(msg.content)
    assert payload["path"] == str(inside)


def test_task_executor_rewrites_relative_path_to_task_root(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(
        "forge.tools.executor.resolve_tool_runtime_policy",
        lambda start=None: ToolRuntimePolicy(
            shell_timeout_seconds=30.0,
            allowed_roots=(workspace.resolve(),),
        ),
    )
    exec_ = _make_executor(_ReadFileTool(), task_workspace_root=workspace)
    call = ToolCall(id="c_rel", name="read_file", arguments={"path": "."})

    msg = exec_.execute(call)

    payload = json.loads(msg.content)
    assert payload["path"] == str(workspace.resolve())


def test_task_executor_fills_missing_path_with_task_root(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(
        "forge.tools.executor.resolve_tool_runtime_policy",
        lambda start=None: ToolRuntimePolicy(
            shell_timeout_seconds=30.0,
            allowed_roots=(workspace.resolve(),),
        ),
    )
    exec_ = _make_executor(_ReadFileTool(), task_workspace_root=workspace)
    call = ToolCall(id="c_missing", name="read_file", arguments={})

    msg = exec_.execute(call)

    payload = json.loads(msg.content)
    assert payload["path"] == str(workspace.resolve())


def test_shell_timeout_is_capped_by_workspace_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        "forge.tools.executor.resolve_tool_runtime_policy",
        lambda start=None: ToolRuntimePolicy(
            shell_timeout_seconds=12.0,
            allowed_roots=(Path.cwd().resolve(),),
        ),
    )
    exec_ = _make_executor(_ShellTool())
    call = ToolCall(
        id="c3",
        name="shell",
        arguments={"command": "echo hi", "timeout_seconds": 99},
    )
    msg = exec_.execute(call)
    payload = json.loads(msg.content)
    assert payload["timeout_seconds"] == 12.0
