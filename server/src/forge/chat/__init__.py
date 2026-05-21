"""Chat turn 业务编排.

把原 _stream_chat 的巨函数拆成:
    Preparer (DB) -> Assembler (上下文) -> Runner (agent 循环) -> Finalizer (落库 + 终态)
顶层由 TurnOrchestrator 串起来.

唯一对外入口:
    from forge.chat import build_turn_orchestrator, get_active_streams
"""

from .orchestrator import TurnOrchestrator, build_turn_orchestrator, get_active_streams

__all__ = [
    "TurnOrchestrator",
    "build_turn_orchestrator",
    "get_active_streams",
]
