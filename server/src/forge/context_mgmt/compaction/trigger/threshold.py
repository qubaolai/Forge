"""ThresholdTrigger: token 使用率超阈值时触发.

等价于现有 ContextAssembler.should_compact():
    - history_messages_dropped > 0 (历史被裁剪过)
    - 或 usage_ratio > threshold (默认 0.85)
"""

from __future__ import annotations

from forge.context_mgmt.types import ContextSnapshot

DEFAULT_THRESHOLD = 0.85


class ThresholdTrigger:
    """按 token 使用率判断是否触发压缩."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        assert 0.0 < threshold < 1.0
        self._threshold = threshold

    @property
    def name(self) -> str:
        return f"threshold({self._threshold:.2f})"

    def should_compact(self, snapshot: ContextSnapshot) -> bool:
        if snapshot.history_messages_dropped > 0:
            return True
        return snapshot.usage_ratio > self._threshold
