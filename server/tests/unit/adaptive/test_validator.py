"""TaskGraphValidator 规则测试。"""

from __future__ import annotations

from pathlib import Path

from forge.adaptive.models import TaskGraph, TaskKind, TaskNode
from forge.adaptive.options import HardCaps, TaskOptions
from forge.adaptive.validator import TaskGraphValidator


def _options(workspace_path: str, *, max_steps_per_task: int = 50) -> TaskOptions:
    return TaskOptions(
        allow_write=True,
        allow_parallel=True,
        max_agents=4,
        writer_mode="isolated_worktree",
        verifier_cmd=None,
        workspace_path=workspace_path,
        hard_caps=HardCaps(max_steps_per_task=max_steps_per_task),
    )


def _validator() -> TaskGraphValidator:
    return TaskGraphValidator(
        tool_allowlist=["read_file", "write_file", "edit_file", "run_tests", "shell", "list_directory"]
    )


def test_validator_accepts_valid_graph(tmp_path: Path) -> None:
    graph = TaskGraph(
        nodes={
            "t1": TaskNode(
                id="t1",
                title="读",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
            ),
            "t2": TaskNode(
                id="t2",
                title="写",
                kind=TaskKind.WRITE,
                allowed_tools=("write_file", "edit_file"),
                read_scope=("server",),
                write_scope=("server",),
                deps=("t1",),
            ),
        }
    )
    result = _validator().validate(graph, options=_options(str(tmp_path)))
    assert result.valid is True


def test_validator_rejects_unknown_dep_and_cycle(tmp_path: Path) -> None:
    graph = TaskGraph(
        nodes={
            "a": TaskNode(
                id="a",
                title="a",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
                deps=("missing",),
            ),
            "b": TaskNode(
                id="b",
                title="b",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
                deps=("c",),
            ),
            "c": TaskNode(
                id="c",
                title="c",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
                deps=("b",),
            ),
        }
    )
    result = _validator().validate(graph, options=_options(str(tmp_path)))
    codes = {issue.code for issue in result.issues}
    assert "deps_missing" in codes
    assert "dag_cycle" in codes


def test_validator_rejects_allowlist_and_scope_and_kind_rules(tmp_path: Path) -> None:
    graph = TaskGraph(
        nodes={
            "t1": TaskNode(
                id="t1",
                title="read but write",
                kind=TaskKind.READ,
                allowed_tools=("write_file", "unknown_tool"),
                read_scope=("server",),
                write_scope=("../outside",),
            )
        }
    )
    result = _validator().validate(graph, options=_options(str(tmp_path)))
    codes = {issue.code for issue in result.issues}
    assert "tool_not_allowed" in codes
    assert "write_scope_out_of_root" in codes
    assert "read_has_write_scope" in codes
    assert "read_has_write_tool" in codes


def test_validator_rejects_max_steps_and_same_wave_write_conflict(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    graph = TaskGraph(
        nodes={
            "t1": TaskNode(
                id="t1",
                title="w1",
                kind=TaskKind.WRITE,
                allowed_tools=("write_file",),
                read_scope=("server",),
                write_scope=("server/src",),
                max_steps=99,
            ),
            "t2": TaskNode(
                id="t2",
                title="w2",
                kind=TaskKind.WRITE,
                allowed_tools=("write_file",),
                read_scope=("server",),
                write_scope=("server/src/api",),
            ),
        }
    )
    result = _validator().validate(graph, options=_options(workspace, max_steps_per_task=10))
    codes = [issue.code for issue in result.issues]
    assert "max_steps_exceeded" in codes
    assert "same_wave_write_conflict" in codes
