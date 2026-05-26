"""VerbatimPolicy: 保持原样, 不做任何处理 (当前行为, 阶段 1 默认).

阶段 2 引入 TruncatingPolicy / EvictingPolicy / SummarizingPolicy 时,
chat 默认会切换到 TruncatingPolicy.
"""

from __future__ import annotations

from forge.context_mgmt.protocols import TokenMeter, ToolResultPolicy


class VerbatimPolicy(ToolResultPolicy):
    @property
    def name(self) -> str:
        return "verbatim"

    def process(
        self,
        tool_name: str,
        original_content: str,
        token_budget: int,
        meter: TokenMeter,
    ) -> str:
        return original_content
