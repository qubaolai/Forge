"""AdaptiveRunStore 状态持久化与事件回放测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.adaptive import events
from forge.adaptive.models import AdaptiveRun, RunStatus, TaskGraph, TaskKind, TaskNode
from forge.adaptive.store import AdaptiveRunStore

pytestmark = pytest.mark.asyncio


async def test_save_and_load_run_snapshot(tmp_path: Path) -> None:
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    run = AdaptiveRun(
        run_id="run_1",
        workspace_path=str(tmp_path),
        goal="修复接口超时问题",
        task_graph=TaskGraph(
            nodes={
                "t1": TaskNode(
                    id="t1",
                    title="读取代码",
                    kind=TaskKind.READ,
                    allowed_tools=("read_file",),
                    read_scope=("server/src",),
                    write_scope=(),
                )
            }
        ),
    )

    await store.save_run(run)
    loaded = await store.load_run("run_1")

    assert loaded is not None
    assert loaded.run_id == "run_1"
    assert loaded.goal == "修复接口超时问题"
    assert loaded.task_graph is not None
    assert "t1" in loaded.task_graph.nodes


async def test_event_replay_after_event_id(tmp_path: Path) -> None:
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    run = AdaptiveRun(run_id="run_2", workspace_path=str(tmp_path), goal="目标")
    await store.save_run(run)

    first = await store.append_event("run_2", events.RUN_CREATED, {"x": 1})
    await store.append_event("run_2", events.PLAN_CREATED, {"x": 2})
    await store.append_event("run_2", events.PLAN_VALIDATED, {"x": 3})

    replayed = await store.list_events("run_2", after_event_id=first.id)
    assert [evt.type for evt in replayed] == [events.PLAN_CREATED, events.PLAN_VALIDATED]


async def test_has_event_validates_cursor(tmp_path: Path) -> None:
    """B4/P1-8: has_event 用于 SSE 入口校验 after_event_id 是否过期。"""
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    run = AdaptiveRun(run_id="run_cursor", workspace_path=str(tmp_path), goal="g")
    await store.save_run(run)

    evt = await store.append_event("run_cursor", events.RUN_CREATED, {})
    assert await store.has_event("run_cursor", evt.id) is True
    assert await store.has_event("run_cursor", "evt_does_not_exist") is False


async def test_owner_filter_in_list_runs(tmp_path: Path) -> None:
    """N1: list_runs 按 owner_user_id 过滤。"""
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    a = AdaptiveRun(run_id="run_a", workspace_path=str(tmp_path), goal="ga", owner_user_id="alice")
    b = AdaptiveRun(run_id="run_b", workspace_path=str(tmp_path), goal="gb", owner_user_id="bob")
    await store.save_run(a)
    await store.save_run(b)

    only_alice = await store.list_runs(owner_user_id="alice")
    assert {r.run_id for r in only_alice} == {"run_a"}

    everyone = await store.list_runs()
    assert {r.run_id for r in everyone} == {"run_a", "run_b"}


async def test_transition_status_and_reject_invalid_transition(tmp_path: Path) -> None:
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    run = AdaptiveRun(run_id="run_3", workspace_path=str(tmp_path), goal="目标")
    await store.save_run(run)

    updated = await store.transition_status("run_3", RunStatus.PLANNING)
    assert updated.status == RunStatus.PLANNING

    with pytest.raises(ValueError, match="非法状态流转"):
        await store.transition_status("run_3", RunStatus.COMPLETED)
