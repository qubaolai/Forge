from __future__ import annotations

import pytest

from forge.agents.base import AgentEvent
from forge.orchestration.workflow import Artifact, ArtifactStore, ArtifactType
from forge.orchestration.workflow.executor import (
    ChatTurnPhaseExecutor,
    FallbackPhaseExecutor,
    PhaseExecutionResult,
    SyntheticPhaseExecutor,
    _build_phase_prompt,
)
from forge.orchestration.workflow.models import (
    WorkflowPhaseState,
    WorkflowState,
)


class _BoomExecutor:
    async def execute(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        raise RuntimeError("boom")


class _OkExecutor:
    async def execute(self, **kwargs):  # noqa: ANN003
        state = kwargs["state"]
        phase = kwargs["phase"]
        return PhaseExecutionResult(
            status="done",
            output=f"{state.workflow_id}:{phase.id}",
            metadata={"executor": "ok"},
        )


class _FailedExecutor:
    async def execute(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        return PhaseExecutionResult(
            status="failed",
            output="primary failed",
            finish_reason="error",
            metadata={"executor": "failed"},
        )


def _sample_state() -> WorkflowState:
    return WorkflowState(
        workflow_id="wf_test",
        workspace_path="/tmp/ws",
        template_id="quick_fix",
        template_name="Quick Fix",
        intent="quick_fix",
        input_message="fix bug",
        phases=[
            WorkflowPhaseState(id="p1", role="developer", task="do thing"),
        ],
    )


@pytest.mark.asyncio
async def test_synthetic_phase_executor_done():
    exe = SyntheticPhaseExecutor()
    state = _sample_state()
    result = await exe.execute(state=state, phase=state.phases[0], phase_index=0)
    assert result.status == "done"
    assert "synthetic" in result.output
    assert result.metadata["executor"] == "synthetic"


@pytest.mark.asyncio
async def test_fallback_phase_executor_uses_primary():
    exe = FallbackPhaseExecutor(primary=_OkExecutor())
    state = _sample_state()
    result = await exe.execute(state=state, phase=state.phases[0], phase_index=0)
    assert result.status == "done"
    assert result.metadata["executor"] == "ok"


@pytest.mark.asyncio
async def test_fallback_phase_executor_degrades_on_error():
    exe = FallbackPhaseExecutor(primary=_BoomExecutor(), fallback=SyntheticPhaseExecutor())
    state = _sample_state()
    result = await exe.execute(state=state, phase=state.phases[0], phase_index=0)
    assert result.status == "done"
    assert result.metadata["executor"] == "synthetic"
    assert result.metadata["fallback_reason"] == "RuntimeError"


@pytest.mark.asyncio
async def test_fallback_phase_executor_degrades_on_failed_result():
    exe = FallbackPhaseExecutor(primary=_FailedExecutor(), fallback=SyntheticPhaseExecutor())
    state = _sample_state()
    result = await exe.execute(state=state, phase=state.phases[0], phase_index=0)
    assert result.status == "done"
    assert result.metadata["executor"] == "synthetic"
    assert result.metadata["fallback_reason"] == "primary_failed"


@pytest.mark.asyncio
async def test_chat_turn_executor_routes_by_role():
    class _FakeTurnOrchestrator:
        def __init__(self) -> None:
            self.called: dict | None = None

        async def run_turn(self, **kwargs):  # noqa: ANN003
            self.called = kwargs
            yield AgentEvent(
                "done",
                {
                    "finish_reason": "stop",
                    "content": "ok",
                    "usage": {"total_tokens": 1},
                },
            )

    state = _sample_state()
    state.phases[0].role = "qa"
    state.metadata["role_artifacts"] = {
        "developer": [{"phase_id": "p0", "summary": "已完成定位", "artifact_type": "phase_output"}]
    }

    exe = ChatTurnPhaseExecutor()
    fake = _FakeTurnOrchestrator()
    exe._orchestrator = fake  # type: ignore[attr-defined]

    result = await exe.execute(state=state, phase=state.phases[0], phase_index=0)
    assert result.status == "done"
    assert fake.called is not None
    overrides = fake.called["overrides"]
    assert overrides.role == "qa"
    assert "你是 QA 角色" in overrides.system_prompt
    assert "write_file" not in [t.name for t in (overrides.tools or ())]


@pytest.mark.asyncio
async def test_phase_prompt_only_lists_summary_not_full_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    state = WorkflowState(
        workflow_id="wf_prompt",
        workspace_path=str(workspace),
        template_id="quick_fix",
        template_name="Quick Fix",
        intent="quick_fix",
        input_message="fix bug",
        phases=[
            WorkflowPhaseState(
                id="locate",
                role="developer",
                task="locate",
                status="done",
                artifact_id="art_locate",
            ),
            WorkflowPhaseState(id="patch", role="developer", task="patch"),
        ],
    )
    state.metadata["role_artifacts"] = {
        "developer": [
            {
                "phase_id": "locate",
                "artifact_id": "art_locate",
                "artifact_type": "phase_output",
                "summary": "only summary",
            }
        ]
    }
    store = ArtifactStore()
    await store.save(
        Artifact(
            id="art_locate",
            workflow_id="wf_prompt",
            phase_id="locate",
            type=ArtifactType.PHASE_OUTPUT,
            title="locate",
            summary="only summary",
            payload={"full_payload_secret": "SHOULD_NOT_APPEAR"},
            created_by_role="developer",
        ),
        workspace_root=workspace,
    )

    prompt = await _build_phase_prompt(
        state=state,
        phase=state.phases[1],
        phase_index=1,
        artifact_store=store,
    )
    assert "art_locate" in prompt
    assert "only summary" in prompt
    assert "full_payload_secret" not in prompt
    assert "SHOULD_NOT_APPEAR" not in prompt
