from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forge.core.exceptions import Forbidden
from forge.orchestration.workflow import (
    PhaseExecutionResult,
    WorkflowOrchestrator,
)
from forge.orchestration.workflow.models import WorkflowPhaseState, WorkflowState


@pytest.mark.asyncio
async def test_workflow_start_pause_resume_complete(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    orchestrator = WorkflowOrchestrator()
    state = await orchestrator.start(
        message="修复一个小 bug",
        workspace_root=ws,
        pause_after_phase=True,
    )

    assert state.template_id == "quick_fix"
    assert state.status == "paused"
    assert state.current_phase_index == 1
    assert state.phases[0].status == "done"

    resumed = await orchestrator.resume(
        workflow_id=state.workflow_id,
        pause_after_phase=True,  # 让续跑也走同步路径, 方便断言终态
    )
    # 至此应该走完剩余阶段 -> completed (或再次 paused 取决于 phases 数)
    if resumed.status == "paused":
        await orchestrator.resume(workflow_id=state.workflow_id, pause_after_phase=True)
    final = await orchestrator.get_state(state.workflow_id)
    while final.status not in {"completed", "failed"}:
        await orchestrator.resume(workflow_id=state.workflow_id, pause_after_phase=True)
        final = await orchestrator.get_state(state.workflow_id)
    assert final.status == "completed"
    assert final.current_phase_index == len(final.phases)

    events = await orchestrator.list_events(workflow_id=state.workflow_id)
    event_types = [e.type for e in events]
    assert "workflow.started" in event_types
    assert "workflow.paused" in event_types
    assert "workflow.resumed" in event_types
    assert event_types[-1] == "workflow.completed"


@pytest.mark.asyncio
async def test_workflow_abort_on_paused(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    orchestrator = WorkflowOrchestrator()
    state = await orchestrator.start(
        message="写一个回答",
        workspace_root=ws,
        pause_after_phase=True,
    )
    aborted = await orchestrator.abort(workflow_id=state.workflow_id, reason="manual")

    assert aborted.status == "aborted"

    events = await orchestrator.list_events(workflow_id=state.workflow_id)
    assert any(e.type == "workflow.aborted" for e in events)


@pytest.mark.asyncio
async def test_workflow_abort_actually_stops_background_run(tmp_path: Path, monkeypatch):
    """abort 必须真正打断后台 task — _run 在下一个 phase 边界感知到 abort_event
    后立即退出, 不再继续后续 phase."""
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    abort_signal = asyncio.Event()
    phases_executed: list[str] = []

    class _SlowExecutor:
        async def execute(
            self, *, state: WorkflowState, phase: WorkflowPhaseState, phase_index: int
        ):
            _ = phase_index
            phases_executed.append(phase.id)
            # 第一个 phase 跑完后等待外部 abort
            if len(phases_executed) == 1:
                abort_signal.set()
                await asyncio.sleep(0.2)
            return PhaseExecutionResult(
                status="done",
                output=f"{state.workflow_id}:{phase.id}",
                metadata={"executor": "slow"},
            )

    orchestrator = WorkflowOrchestrator(phase_executor=_SlowExecutor())
    state = await orchestrator.start(
        message="修复一个 bug",
        workspace_root=ws,
        pause_after_phase=False,  # async, 后台 task
    )
    # 等第一个 phase 开始
    await asyncio.wait_for(abort_signal.wait(), timeout=2.0)
    aborted = await orchestrator.abort(workflow_id=state.workflow_id, reason="user")
    assert aborted.status == "aborted"

    # quick_fix 模板有 3 个 phase, 只允许跑掉 1 个就被打断
    assert len(phases_executed) <= 2, (
        f"abort 未生效, 执行了 {len(phases_executed)} 个 phase: {phases_executed}"
    )

    # 落盘 state 是 aborted, 不会被 _run 后续 save 覆盖
    persisted = await orchestrator.get_state(state.workflow_id)
    assert persisted.status == "aborted"


@pytest.mark.asyncio
async def test_workflow_async_start_returns_immediately(tmp_path: Path, monkeypatch):
    """pause_after_phase=False 时 start 不阻塞: 立即返回 running, 后台跑."""
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    started_at = asyncio.get_event_loop().time()

    class _SlowExecutor:
        async def execute(self, **kwargs):
            await asyncio.sleep(0.3)
            return PhaseExecutionResult(
                status="done",
                output="ok",
                metadata={"executor": "slow"},
            )

    orchestrator = WorkflowOrchestrator(phase_executor=_SlowExecutor())
    state = await orchestrator.start(
        message="跑回归测试",
        workspace_root=ws,
        pause_after_phase=False,
    )
    elapsed = asyncio.get_event_loop().time() - started_at
    assert elapsed < 0.2, f"start 不应阻塞, 耗时 {elapsed:.3f}s"
    assert state.status == "running"

    # 后台跑完
    final = await orchestrator.wait_for(state.workflow_id, timeout=5.0)
    assert final.status == "completed"


@pytest.mark.asyncio
async def test_workflow_events_reattach_from_event_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    orchestrator = WorkflowOrchestrator()
    state = await orchestrator.start(
        message="解释一个问题",
        workspace_root=ws,
        pause_after_phase=True,
    )
    # 走完剩余 phase
    while True:
        cur = await orchestrator.get_state(state.workflow_id)
        if cur.status in {"completed", "failed", "aborted"}:
            break
        await orchestrator.resume(workflow_id=state.workflow_id, pause_after_phase=True)

    all_events = await orchestrator.list_events(workflow_id=state.workflow_id)
    assert len(all_events) >= 3
    marker = all_events[1].id

    replayed = await orchestrator.list_events(
        workflow_id=state.workflow_id,
        from_event_id=marker,
    )
    assert replayed
    assert all(evt.id != marker for evt in replayed)
    assert replayed[0].id == all_events[2].id


@pytest.mark.asyncio
async def test_workflow_owner_isolation(tmp_path: Path, monkeypatch):
    """非 owner 拿不到/操纵不了别人的 workflow."""
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    orchestrator = WorkflowOrchestrator()
    state = await orchestrator.start(
        message="解释一下",
        workspace_root=ws,
        pause_after_phase=True,
        owner_user_id="alice",
    )
    # owner 自己可读
    me = await orchestrator.get_state(state.workflow_id, requester_user_id="alice")
    assert me.workflow_id == state.workflow_id

    with pytest.raises(Forbidden):
        await orchestrator.get_state(state.workflow_id, requester_user_id="bob")
    with pytest.raises(Forbidden):
        await orchestrator.resume(workflow_id=state.workflow_id, requester_user_id="bob")
    with pytest.raises(Forbidden):
        await orchestrator.abort(workflow_id=state.workflow_id, requester_user_id="bob")


@pytest.mark.asyncio
async def test_phase_skipped_event_on_resume(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    orchestrator = WorkflowOrchestrator()
    state = await orchestrator.start(
        message="修复一个小 bug",
        workspace_root=ws,
        pause_after_phase=True,
    )
    cur = await orchestrator.get_state(state.workflow_id)
    # 人工标记当前 phase 为 done, 续跑时应该 emit phase.skipped.
    cur.phases[cur.current_phase_index].status = "done"
    await orchestrator._store.save_state(cur)  # noqa: SLF001
    await orchestrator.resume(workflow_id=state.workflow_id, pause_after_phase=True)

    events = await orchestrator.list_events(workflow_id=state.workflow_id)
    skipped = [e for e in events if e.type == "phase.skipped"]
    assert skipped
    assert skipped[-1].payload.get("reason") == "already_done"


@pytest.mark.asyncio
async def test_phase_completed_emits_artifact_id_not_full_artifact(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    orchestrator = WorkflowOrchestrator()
    state = await orchestrator.start(
        message="修复一个小 bug",
        workspace_root=ws,
        pause_after_phase=True,
    )

    events = await orchestrator.list_events(workflow_id=state.workflow_id)
    completed = [e for e in events if e.type == "phase.completed"]
    assert completed
    payload = completed[0].payload
    assert isinstance(payload.get("artifact_id"), str)
    assert isinstance(payload.get("artifact_summary"), str)
    assert "artifact" not in payload
