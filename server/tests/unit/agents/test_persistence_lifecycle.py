"""RunStorePersistenceLifecycle 单元测试.

聚焦行为:
    - after_step 落 step_completed 事件
    - on_tool_result 大产物落 artifact + 回灌占位 message
    - on_tool_result 小产物不动 (None 返回)
    - on_complete 转 completed
    - on_error 转 failed
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.agents.lifecycle import (
    RunContext,
    RunResult,
    StepContext,
    StepOutcome,
)
from forge.agents.persistence_lifecycle import RunStorePersistenceLifecycle
from forge.core.types.message import Message, ToolCall
from forge.infrastructure.run_store import RunStore


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(base_dir=tmp_path, workspace_path=str(tmp_path / "ws"))


@pytest.mark.asyncio
async def test_lifecycle_attach_and_step_event(store: RunStore) -> None:
    record = await store.create_run(mode="plan_exec", goal="hello")
    lc = RunStorePersistenceLifecycle(store, record.run_id)

    await lc.on_start(RunContext(run_id=record.run_id, mode="plan_exec"))

    tc = ToolCall(id="tc1", name="read_file", arguments={"path": "x"})
    outcome = StepOutcome(
        step_index=0,
        content="hello",
        tool_calls=[tc],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        finish_reason="tool_calls",
        duration_ms=12.5,
    )
    step_ctx = StepContext(step_index=0, max_steps=10, messages_count=3)
    await lc.after_step(step_ctx, outcome)

    events = await store.list_events(record.run_id)
    types = [e.type for e in events]
    assert "run_started" in types
    assert "lifecycle_attached" in types
    assert "step_completed" in types

    step_event = next(e for e in events if e.type == "step_completed")
    assert step_event.payload["step_index"] == 0
    assert step_event.payload["finish_reason"] == "tool_calls"
    assert step_event.payload["tool_calls"][0]["name"] == "read_file"


@pytest.mark.asyncio
async def test_on_tool_result_replaces_large_payload(store: RunStore) -> None:
    record = await store.create_run(mode="plan_exec")
    lc = RunStorePersistenceLifecycle(
        store, record.run_id, large_artifact_threshold_bytes=100
    )

    tc = ToolCall(id="tc1", name="read_file", arguments={"path": "huge.txt"})
    big_content = "X" * 1024
    msg = Message(role="tool", content=big_content, tool_call_id="tc1", name="read_file")

    replaced = await lc.on_tool_result(tc, msg)
    assert replaced is not None
    assert replaced.content.startswith("[artifact:")
    assert "XXX" in replaced.content  # summary 含原内容前缀

    artifacts = await store.list_artifacts(record.run_id)
    assert len(artifacts) == 1
    assert artifacts[0].kind == "tool_output"
    assert artifacts[0].payload["content"] == big_content

    events = await store.list_events(record.run_id)
    assert any(e.type == "artifact_created" for e in events)


@pytest.mark.asyncio
async def test_on_tool_result_keeps_small_payload(store: RunStore) -> None:
    record = await store.create_run(mode="plan_exec")
    lc = RunStorePersistenceLifecycle(
        store, record.run_id, large_artifact_threshold_bytes=100
    )

    tc = ToolCall(id="tc1", name="read_file", arguments={})
    msg = Message(role="tool", content="tiny", tool_call_id="tc1", name="read_file")
    replaced = await lc.on_tool_result(tc, msg)
    assert replaced is None  # 不替换

    arts = await store.list_artifacts(record.run_id)
    assert arts == []


@pytest.mark.asyncio
async def test_on_complete_marks_completed(store: RunStore) -> None:
    record = await store.create_run(mode="plan_exec")
    lc = RunStorePersistenceLifecycle(store, record.run_id)

    await lc.on_complete(RunResult(finish_reason="stop", content="done"))
    reloaded = await store.load_run(record.run_id)
    assert reloaded is not None
    assert reloaded.status == "completed"


@pytest.mark.asyncio
async def test_on_error_marks_failed(store: RunStore) -> None:
    record = await store.create_run(mode="plan_exec")
    lc = RunStorePersistenceLifecycle(store, record.run_id)

    await lc.on_error(RuntimeError("boom"), RunResult(error_message="boom"))
    reloaded = await store.load_run(record.run_id)
    assert reloaded is not None
    assert reloaded.status == "failed"

    events = await store.list_events(record.run_id)
    status_changed = [e for e in events if e.type == "run_status_changed"]
    assert any(
        e.payload.get("error") == "boom" and e.payload.get("to_status") == "failed"
        for e in status_changed
    )
