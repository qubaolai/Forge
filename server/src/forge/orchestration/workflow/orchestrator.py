"""WorkflowOrchestrator — stub for backward compat. Replaced by AdaptiveRunOrchestrator."""

from __future__ import annotations
from typing import Any


class WorkflowOrchestrator:
    """Legacy orchestrator stub. Use adaptive.orchestrator.AdaptiveRunOrchestrator instead."""

    async def start(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("WorkflowOrchestrator is deprecated. Use AdaptiveRunOrchestrator.")

    async def abort(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("WorkflowOrchestrator is deprecated.")
