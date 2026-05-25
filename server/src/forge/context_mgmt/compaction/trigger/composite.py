"""CompositeTrigger: OR 逻辑组合多个 Trigger."""

from __future__ import annotations

from forge.context_mgmt.protocols import CompactionTrigger
from forge.context_mgmt.types import ContextSnapshot


class CompositeTrigger:
    """任一子 trigger 满足 → 触发."""

    def __init__(self, triggers: list[CompactionTrigger]) -> None:
        assert triggers, "CompositeTrigger 至少需要一个子 trigger"
        self._triggers = triggers

    @property
    def name(self) -> str:
        return f"composite({','.join(t.name for t in self._triggers)})"

    def should_compact(self, snapshot: ContextSnapshot) -> bool:
        return any(t.should_compact(snapshot) for t in self._triggers)
