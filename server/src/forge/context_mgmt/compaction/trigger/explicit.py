"""ExplicitTrigger: 始终返回 True, 用于 compact_now 场景."""

from __future__ import annotations

from forge.context_mgmt.protocols import CompactionTrigger
from forge.context_mgmt.types import ContextSnapshot


class ExplicitTrigger(CompactionTrigger):
    """显式触发, 不做任何判断."""

    @property
    def name(self) -> str:
        return "explicit"

    def should_compact(self, snapshot: ContextSnapshot) -> bool:
        return True
