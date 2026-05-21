"""ContextVars shared by workflow-aware agent tools."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

CURRENT_WORKFLOW_ORCHESTRATOR: ContextVar[Any | None] = ContextVar(
    "CURRENT_WORKFLOW_ORCHESTRATOR",
    default=None,
)
CURRENT_WORKFLOW_ID: ContextVar[str | None] = ContextVar("CURRENT_WORKFLOW_ID", default=None)
SUBAGENT_DEPTH: ContextVar[int] = ContextVar("SUBAGENT_DEPTH", default=0)


@contextmanager
def workflow_tool_context(orchestrator: Any, workflow_id: str) -> Iterator[None]:
    orch_token = CURRENT_WORKFLOW_ORCHESTRATOR.set(orchestrator)
    wf_token = CURRENT_WORKFLOW_ID.set(workflow_id)
    try:
        yield
    finally:
        CURRENT_WORKFLOW_ID.reset(wf_token)
        CURRENT_WORKFLOW_ORCHESTRATOR.reset(orch_token)
