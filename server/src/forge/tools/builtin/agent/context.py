"""Agent tool 共享 ContextVar.

阶段 8 起仅保留 SUBAGENT_DEPTH (spawn_subagent 嵌套深度).
之前 workflow orchestrator 相关变量随 forge.orchestration 一并删除.
"""

from __future__ import annotations

from contextvars import ContextVar

SUBAGENT_DEPTH: ContextVar[int] = ContextVar("SUBAGENT_DEPTH", default=0)

__all__ = ["SUBAGENT_DEPTH"]
