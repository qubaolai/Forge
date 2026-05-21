"""Workflow package — stub for backward compat. Will be superseded by adaptive/."""

from .artifact import Artifact, ArtifactType
from .artifact_store import ArtifactStore
from .models import (
    WorkflowPhaseState,
    WorkflowPhaseTemplate,
    WorkflowState,
    WorkflowTemplate,
)
from .orchestrator import WorkflowOrchestrator
from .template_loader import TemplateLoader

# PhaseExecutionResult / WorkflowPhaseExecutor are defined in executor.py which
# depends on forge.chat. Import lazily to avoid circular imports at collection time.
def __getattr__(name: str):  # noqa: N807
    if name in ("PhaseExecutionResult", "WorkflowPhaseExecutor"):
        from .executor import PhaseExecutionResult, WorkflowPhaseExecutor  # noqa: PLC0415
        return {"PhaseExecutionResult": PhaseExecutionResult, "WorkflowPhaseExecutor": WorkflowPhaseExecutor}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Artifact",
    "ArtifactStore",
    "ArtifactType",
    "PhaseExecutionResult",
    "TemplateLoader",
    "WorkflowOrchestrator",
    "WorkflowPhaseExecutor",
    "WorkflowPhaseState",
    "WorkflowPhaseTemplate",
    "WorkflowState",
    "WorkflowTemplate",
]
