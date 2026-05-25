"""TokenMeter: 统一的 token 计量入口.

包装 forge.llm.token_counter.TokenCounter, 在 ContextMgmt 内部使用,
让上下文管理系统不直接依赖 llm 子系统的具体实现.
"""

from __future__ import annotations

from forge.context_mgmt.protocols import TokenMeter
from forge.core.types.message import Message
from forge.llm.token_counter import TokenCounter, get_token_counter


class DefaultTokenMeter:
    """默认 TokenMeter 实现, 委托 llm.token_counter.TokenCounter."""

    def __init__(self, counter: TokenCounter | None = None) -> None:
        self._counter = counter or get_token_counter()

    def count_text(self, text: str) -> int:
        return self._counter.count_text(text)

    def count_messages(self, messages: list[Message]) -> int:
        return self._counter.count_messages(messages)


_DEFAULT_METER: TokenMeter | None = None


def get_token_meter() -> TokenMeter:
    """返回全局 TokenMeter 单例."""
    global _DEFAULT_METER
    if _DEFAULT_METER is None:
        _DEFAULT_METER = DefaultTokenMeter()
    return _DEFAULT_METER


def reset_token_meter() -> None:
    """单测用."""
    global _DEFAULT_METER
    _DEFAULT_METER = None
