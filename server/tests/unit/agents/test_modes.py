from __future__ import annotations

import pytest

from forge.agents.modes import MultiAgentMode, SingleAgentMode
from forge.orchestration.workflow import PhaseExecutionResult
from forge.orchestration.workflow.models import WorkflowPhaseState, WorkflowState


class _RecordingExecutor:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def execute(self, **kwargs):  # noqa: ANN003
        phase = kwargs["phase"]
        self.calls.append(f"{self.name}:{phase.id}")
        return PhaseExecutionResult(
            status="done",
            output=self.name,
            metadata={"executor_id": self.name},
        )


def _state() -> WorkflowState:
    return WorkflowState(
        workflow_id="wf_modes",
        workspace_path="/tmp/workspace",
        template_id="quick_fix",
        template_name="Quick Fix",
        intent="quick_fix",
        input_message="fix",
        phases=[
            WorkflowPhaseState(id="p1", role="developer", task="one"),
            WorkflowPhaseState(id="p2", role="qa", task="two"),
        ],
    )


@pytest.mark.asyncio
async def test_light_mode_keeps_one_orchestrator():
    calls: list[str] = []
    created = 0

    def factory():
        nonlocal created
        created += 1
        return _RecordingExecutor(f"exec_{created}", calls)

    mode = SingleAgentMode()
    state = _state()
    await mode.execute_phase(
        state=state,
        phase=state.phases[0],
        phase_index=0,
        executor_factory=factory,
    )
    await mode.execute_phase(
        state=state,
        phase=state.phases[1],
        phase_index=1,
        executor_factory=factory,
    )

    assert created == 1
    assert calls == ["exec_1:p1", "exec_1:p2"]


@pytest.mark.asyncio
async def test_heavy_mode_uses_independent_agents_per_phase():
    calls: list[str] = []
    created = 0

    def factory():
        nonlocal created
        created += 1
        return _RecordingExecutor(f"exec_{created}", calls)

    mode = MultiAgentMode()
    state = _state()
    await mode.execute_phase(
        state=state,
        phase=state.phases[0],
        phase_index=0,
        executor_factory=factory,
    )
    await mode.execute_phase(
        state=state,
        phase=state.phases[1],
        phase_index=1,
        executor_factory=factory,
    )

    assert created == 2
    assert calls == ["exec_1:p1", "exec_2:p2"]
