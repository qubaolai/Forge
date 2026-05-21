"""Token 计数: ContextBuilder 预算控制用.

提供:
    - TokenCounter Protocol: 业务层只用这个
    - TiktokenCounter:        基于 tiktoken
    - HeuristicCounter:       基于字符数
    - get_token_counter():    工厂, 自动降级

策略:
    安全方向 = 宁可高估不可低估 (低估会爆 context window).
    Heuristic 公式按 OpenAI/Anthropic 经验值给中英混排取保守上界.
"""

from __future__ import annotations

import logging
from typing import Protocol

from forge.core.types.message import Message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
class TokenCounter(Protocol):
    """所有方法必须线程安全 / 协程安全 (无内部可变状态或自带锁)."""

    def count_text(self, text: str) -> int: ...

    def count_messages(self, messages: list[Message]) -> int:
        """估算一组消息的 prompt token 数 (含 role / 分隔符开销)."""
        ...


# 每条消息的固定开销 (role + 分隔符), OpenAI cl100k_base 经验值.
# Anthropic 的 message wrapping 略有不同但量级一致, 此值作为统一估计.
_PER_MESSAGE_OVERHEAD = 4


# ---------------------------------------------------------------------------
# Tiktoken 实现 (准确)
# ---------------------------------------------------------------------------
class TiktokenCounter:
    """基于 tiktoken 的 GPT 系编码器.

    cl100k_base 覆盖 gpt-4 / gpt-4o / gpt-3.5-turbo, 也常被用作其他模型的近似.
    对 Claude / Gemini 不是 1:1 准确但量级相近, 用于预算控制足够.
    """

    def __init__(self, encoding_name: str = "cl100k_base"):
        import tiktoken

        self._enc = tiktoken.get_encoding(encoding_name)

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return len(self._enc.encode(text))

    def count_messages(self, messages: list[Message]) -> int:
        total = 0
        for m in messages:
            total += _PER_MESSAGE_OVERHEAD
            total += self.count_text(m.content)
            if m.name:
                total += self.count_text(m.name)
            for tc in m.tool_calls:
                total += self.count_text(tc.name)
                # arguments 是 dict, 这里近似按 JSON 串长度算
                import json

                total += self.count_text(json.dumps(tc.arguments, ensure_ascii=False))
        return total


# ---------------------------------------------------------------------------
# 启发式实现 (粗略但安全, 不依赖第三方)
# ---------------------------------------------------------------------------
class HeuristicCounter:
    """无依赖兜底.

    经验上界:
        - 英文 ~ 4 字符 / token
        - 中文 ~ 1.5 字符 / token (单汉字 1 token, 标点更碎)
        - 保守取 2 字符 / token 作为上界估计
    再加每消息 4 token 的 wrapping 开销.
    """

    _CHARS_PER_TOKEN = 2.0

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return int(len(text) / self._CHARS_PER_TOKEN) + 1

    def count_messages(self, messages: list[Message]) -> int:
        total = 0
        for m in messages:
            total += _PER_MESSAGE_OVERHEAD
            total += self.count_text(m.content)
            if m.name:
                total += self.count_text(m.name)
            for tc in m.tool_calls:
                total += self.count_text(tc.name)
                import json

                total += self.count_text(json.dumps(tc.arguments, ensure_ascii=False))
        return total


# ---------------------------------------------------------------------------
# 工厂: 自动降级 + 全局缓存
# ---------------------------------------------------------------------------
_DEFAULT_COUNTER: TokenCounter | None = None


def get_token_counter() -> TokenCounter:
    """返回全局 TokenCounter 单例 (优先 tiktoken, 缺失则降级 Heuristic)."""
    global _DEFAULT_COUNTER
    if _DEFAULT_COUNTER is not None:
        return _DEFAULT_COUNTER

    try:
        _DEFAULT_COUNTER = TiktokenCounter()
        logger.info("token 计数器: 使用 TiktokenCounter (cl100k_base)")
    except Exception as exc:
        logger.warning(
            "token 计数器: tiktoken 不可用 (%s); 降级到 HeuristicCounter",
            exc,
        )
        _DEFAULT_COUNTER = HeuristicCounter()

    return _DEFAULT_COUNTER
