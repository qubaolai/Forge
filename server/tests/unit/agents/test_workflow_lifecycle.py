"""WorkflowLifecycle 单元测试.

聚焦:
    - 模板校验 (validate_workflow_template)
    - advance_phase 拦截语义 (顺序错乱报错; 正常推进; 终止)
    - workflow_gate HITL 流程 (DecisionRegistry 唤醒)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from forge.agents.hitl import (
    Decision,
    DecisionRegistry,
    reset_decision_registry,
)
from forge.agents.lifecycle import RunContext, StepContext
from forge.agents.workflow_lifecycle import (
    WorkflowLifecycle,
    WorkflowTemplateError,
    validate_workflow_template,
)
from forge.core.types.message import ToolCall
from forge.infrastructure.run_store import RunStore


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_decision_registry()


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(base_dir=tmp_path, workspace_path=str(tmp_path / "ws"))


# ---------------------------------------------------------------------------
# 模板校验
# ---------------------------------------------------------------------------
def test_template_validation_ok() -> None:
    tpl = {
        "phases": [
            {"id": "a", "role": "developer", "task": "x"},
            {"id": "b", "role": "qa", "task": "y"},
        ],
        "gates": [{"after_phase": "a"}],
    }
    validate_workflow_template(tpl)  # not raise


def test_template_validation_empty_phases() -> None:
    with pytest.raises(WorkflowTemplateError):
        validate_workflow_template({"phases": []})


def test_template_validation_duplicate_phase_id() -> None:
    with pytest.raises(WorkflowTemplateError):
        validate_workflow_template({
            "phases": [
                {"id": "a", "role": "developer", "task": "x"},
                {"id": "a", "role": "qa", "task": "y"},
            ],
        })


def test_template_validation_unknown_gate_phase() -> None:
    with pytest.raises(WorkflowTemplateError):
        validate_workflow_template({
            "phases": [{"id": "a", "role": "developer", "task": "x"}],
            "gates": [{"after_phase": "nope"}],
        })


# ---------------------------------------------------------------------------
# advance_phase 拦截
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_advance_phase_normal_progress(store: RunStore) -> None:
    record = await store.create_run(mode="workflow")
    tpl = {
        "phases": [
            {"id": "a", "role": "developer", "task": "x"},
            {"id": "b", "role": "qa", "task": "y"},
        ],
        "gates": [],
    }
    lc = WorkflowLifecycle(tpl, run_id=record.run_id, store=store)
    await lc.on_start(RunContext(run_id=record.run_id, mode="workflow"))

    tc = ToolCall(id="t1", name="advance_phase", arguments={"phase_id": "a"})
    veto = await lc.before_tool_call(
        tc, StepContext(step_index=0, max_steps=10, messages_count=2)
    )
    assert veto is not None and veto.blocked
    payload = json.loads(veto.replacement_message.content)
    assert payload["approved"] is True
    assert payload["completed_phase"] == "a"
    assert payload["next_phase"] == "b"
    assert lc.current_phase["id"] == "b"

    # 第二次完成最后一个 phase -> workflow_completed
    tc2 = ToolCall(id="t2", name="advance_phase", arguments={"phase_id": "b"})
    veto2 = await lc.before_tool_call(
        tc2, StepContext(step_index=1, max_steps=10, messages_count=4)
    )
    payload2 = json.loads(veto2.replacement_message.content)
    assert payload2["approved"] is True
    assert payload2["next"].startswith("全部 phase 已完成")
    assert lc.current_phase is None


@pytest.mark.asyncio
async def test_advance_phase_id_mismatch(store: RunStore) -> None:
    record = await store.create_run(mode="workflow")
    tpl = {
        "phases": [
            {"id": "a", "role": "developer", "task": "x"},
            {"id": "b", "role": "qa", "task": "y"},
        ],
    }
    lc = WorkflowLifecycle(tpl, run_id=record.run_id, store=store)
    tc = ToolCall(id="t1", name="advance_phase", arguments={"phase_id": "wrong"})
    veto = await lc.before_tool_call(
        tc, StepContext(step_index=0, max_steps=10, messages_count=2)
    )
    payload = json.loads(veto.replacement_message.content)
    assert payload["approved"] is False
    assert "wrong" in payload["feedback"]
    assert lc.current_phase["id"] == "a"  # 未推进


@pytest.mark.asyncio
async def test_advance_phase_with_gate_approved(store: RunStore) -> None:
    record = await store.create_run(mode="workflow")
    tpl = {
        "phases": [
            {"id": "a", "role": "developer", "task": "x"},
            {"id": "b", "role": "qa", "task": "y"},
        ],
        "gates": [{"after_phase": "a"}],
    }
    lc = WorkflowLifecycle(tpl, run_id=record.run_id, store=store, ttl_sec=5)

    tc = ToolCall(id="t1", name="advance_phase", arguments={"phase_id": "a"})
    task = asyncio.create_task(
        lc.before_tool_call(tc, StepContext(step_index=0, max_steps=10, messages_count=2))
    )

    # 等 lifecycle 创建 pending
    from forge.agents.hitl import get_decision_registry
    registry = get_decision_registry()
    for _ in range(50):
        await asyncio.sleep(0.02)
        pending = [
            it for it in registry._items.values()  # type: ignore[attr-defined]
            if it.kind == "workflow_gate"
        ]
        if pending:
            break
    assert pending, "lifecycle 未创建 workflow_gate pending decision"
    pending_item = pending[0]
    assert pending_item.payload["completed_phase"] == "a"

    registry.resolve(pending_item.token, Decision(approved=True))
    veto = await asyncio.wait_for(task, timeout=2.0)
    payload = json.loads(veto.replacement_message.content)
    assert payload["approved"] is True
    assert payload["next_phase"] == "b"

    # 事件流应含 workflow_gate_required
    events = await store.list_events(record.run_id)
    types = [e.type for e in events]
    assert "workflow_gate_required" in types


@pytest.mark.asyncio
async def test_advance_phase_with_gate_rejected(store: RunStore) -> None:
    record = await store.create_run(mode="workflow")
    tpl = {
        "phases": [
            {"id": "a", "role": "developer", "task": "x"},
            {"id": "b", "role": "qa", "task": "y"},
        ],
        "gates": [{"after_phase": "a"}],
    }
    lc = WorkflowLifecycle(tpl, run_id=record.run_id, store=store, ttl_sec=5)

    tc = ToolCall(id="t1", name="advance_phase", arguments={"phase_id": "a"})
    task = asyncio.create_task(
        lc.before_tool_call(tc, StepContext(step_index=0, max_steps=10, messages_count=2))
    )

    from forge.agents.hitl import get_decision_registry
    registry = get_decision_registry()
    for _ in range(50):
        await asyncio.sleep(0.02)
        if any(
            it.kind == "workflow_gate"
            for it in registry._items.values()  # type: ignore[attr-defined]
        ):
            break
    pending = next(
        it for it in registry._items.values()  # type: ignore[attr-defined]
        if it.kind == "workflow_gate"
    )
    registry.resolve(pending.token, Decision(approved=False, feedback="发现关键 bug"))

    veto = await asyncio.wait_for(task, timeout=2.0)
    payload = json.loads(veto.replacement_message.content)
    assert payload["approved"] is False
    assert "发现关键 bug" in payload["feedback"]
    # 未推进
    assert lc.current_phase["id"] == "a"
