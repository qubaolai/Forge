"""Agent execution mode abstraction for workflow phases."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from forge.orchestration.workflow.executor import (
    PhaseExecutionResult,
    WorkflowPhaseExecutor,
)
from forge.orchestration.workflow.models import WorkflowPhaseState, WorkflowState

ExecutorFactory = Callable[[], WorkflowPhaseExecutor]


class AgentMode(Protocol):
    name: str

    async def execute_phase(
        self,
        *,
        state: WorkflowState,
        phase: WorkflowPhaseState,
        phase_index: int,
        executor_factory: ExecutorFactory,
    ) -> PhaseExecutionResult: ...
