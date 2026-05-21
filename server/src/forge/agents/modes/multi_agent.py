"""Heavy workflow mode: create an isolated executor per phase."""

from __future__ import annotations

from forge.orchestration.workflow.executor import PhaseExecutionResult
from forge.orchestration.workflow.models import WorkflowPhaseState, WorkflowState

from .base import ExecutorFactory


class MultiAgentMode:
    name = "heavy"

    async def execute_phase(
        self,
        *,
        state: WorkflowState,
        phase: WorkflowPhaseState,
        phase_index: int,
        executor_factory: ExecutorFactory,
    ) -> PhaseExecutionResult:
        executor = executor_factory()
        result = await executor.execute(
            state=state,
            phase=phase,
            phase_index=phase_index,
        )
        result.metadata.setdefault("agent_mode", self.name)
        return result
