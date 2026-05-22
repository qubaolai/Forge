"""Scheduler（M9）测试。"""

from __future__ import annotations

from pathlib import Path

from forge.adaptive.models import TaskGraph, TaskKind, TaskNode
from forge.adaptive.scheduler import Scheduler


def test_scheduler_splits_conflicting_writes_into_separate_waves(tmp_path: Path) -> None:
    graph = TaskGraph(
        nodes={
            "a": TaskNode(
                id="a",
                title="write-a",
                kind=TaskKind.WRITE,
                allowed_tools=("write_file",),
                read_scope=("server",),
                write_scope=("server/src",),
            ),
            "b": TaskNode(
                id="b",
                title="write-b",
                kind=TaskKind.WRITE,
                allowed_tools=("write_file",),
                read_scope=("server",),
                write_scope=("server/src/api",),
            ),
            "c": TaskNode(
                id="c",
                title="read-c",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
            ),
        }
    )
    plan = Scheduler().compute_waves(graph, workspace_path=str(tmp_path))
    # a 与 b 写 scope 冲突，应被拆到不同 wave；c 可与其中一个同 wave。
    assert len(plan.waves) == 2
    first_wave = set(plan.waves[0])
    second_wave = set(plan.waves[1])
    assert {"a", "b"} != first_wave
    assert {"a", "b"} != second_wave
    assert "a" in first_wave.union(second_wave)
    assert "b" in first_wave.union(second_wave)


def test_scheduler_keeps_dependency_order(tmp_path: Path) -> None:
    graph = TaskGraph(
        nodes={
            "t1": TaskNode(
                id="t1",
                title="read",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
            ),
            "t2": TaskNode(
                id="t2",
                title="write",
                kind=TaskKind.WRITE,
                allowed_tools=("write_file",),
                read_scope=("server",),
                write_scope=("server/a",),
                deps=("t1",),
            ),
        }
    )
    plan = Scheduler().compute_waves(graph, workspace_path=str(tmp_path))
    assert plan.waves[0] == ("t1",)
    assert plan.waves[1] == ("t2",)
