"""Light workflow mode: reuse one executor across phases."""

from __future__ import annotations

from forge.orchestration.workflow.executor import (
    PhaseExecutionResult,
    WorkflowPhaseExecutor,
)
from forge.orchestration.workflow.models import WorkflowPhaseState, WorkflowState

from .base import ExecutorFactory


class SingleAgentMode:
    name = "light"

    def __init__(self, executor: WorkflowPhaseExecutor | None = None) -> None:
        self._executor = executor

    async def execute_phase(
        self,
        *,
        state: WorkflowState,
        phase: WorkflowPhaseState,
        phase_index: int,
        executor_factory: ExecutorFactory,
    ) -> PhaseExecutionResult:
        if self._executor is None:
            self._executor = executor_factory()
        result = await self._executor.execute(
            state=state,
            phase=phase,
            phase_index=phase_index,
        )
        result.metadata.setdefault("agent_mode", self.name)
        return result
