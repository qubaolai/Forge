"""SummarizingPolicy: LLM 摘要工具结果, workflow 模式默认.

复用 forge.memory.summary.summarizer.Summarizer 的轻量摘要能力.
若 LLM 不可用 / 摘要失败, 降级到 TruncatingPolicy.
"""

from __future__ import annotations

import logging

from forge.context_mgmt.protocols import TokenMeter, ToolResultPolicy
from forge.context_mgmt.tool_policy.truncating import TruncatingPolicy

logger = logging.getLogger(__name__)


class SummarizingPolicy(ToolResultPolicy):
    """大 tool 结果 → LLM 摘要, 小结果保持原样.

    Note:
        阶段 2 实现: 当 token 数超过 summarize_threshold 时, 才走 LLM 摘要.
        阶段 4 可接入 forge.memory.summary.summarizer 做真实摘要.
        当前为占位实现, 走 TruncatingPolicy 作为安全兜底,
        避免 LLM 调用失败拖慢上下文构建.
    """

    def __init__(
        self,
        summarize_threshold: int = 1500,
        fallback_max_tokens: int = 500,
    ) -> None:
        self._threshold = summarize_threshold
        self._fallback = TruncatingPolicy(max_tokens=fallback_max_tokens)

    @property
    def name(self) -> str:
        return "summarizing"

    def process(
        self,
        tool_name: str,
        original_content: str,
        token_budget: int,
        meter: TokenMeter,
    ) -> str:
        # 当前: 直接降级 Truncating. 后续接 LLM 摘要时换成 try/except 调用 + fallback.
        return self._fallback.process(tool_name, original_content, token_budget, meter)
