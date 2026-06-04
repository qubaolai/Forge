"""Chat turn 业务编排.

把原 _stream_chat 的巨函数拆成:
    Preparer (DB) -> ContextManager (上下文) -> Runner (agent 循环) -> Finalizer (落库 + 终态)
顶层由 TurnOrchestrator 串起来.

唯一对外入口:
    from forge.chat import build_turn_orchestrator
"""

from .orchestrator import TurnOrchestrator, build_turn_orchestrator

__all__ = [
    "TurnOrchestrator",
    "build_turn_orchestrator",
]
