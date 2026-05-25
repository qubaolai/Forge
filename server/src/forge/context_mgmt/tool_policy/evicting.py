"""EvictingPolicy: 替换为占位符, task 模式默认.

适用场景: adaptive 任务执行期间 LLM 看到完整 tool 结果, 跨轮不再需要.
"""

from __future__ import annotations

from forge.context_mgmt.protocols import TokenMeter


class EvictingPolicy:
    """直接替换为占位符, 完全剔除 tool 结果内容."""

    @property
    def name(self) -> str:
        return "evicting"

    def process(
        self,
        tool_name: str,
        original_content: str,
        token_budget: int,
        meter: TokenMeter,
    ) -> str:
        return f"[{tool_name} 已执行, 结果已处理, 此处省略]"
