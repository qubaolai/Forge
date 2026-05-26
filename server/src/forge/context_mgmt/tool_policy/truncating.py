"""TruncatingPolicy: 截断到 MAX tokens, chat 模式默认.

策略:
    - 若 token 数 <= max_tokens 直接返回原文
    - 否则截断后追加 "[... 内容已截断, 完整结果已处理]" 提示
"""

from __future__ import annotations

from forge.context_mgmt.protocols import TokenMeter, ToolResultPolicy


class TruncatingPolicy(ToolResultPolicy):
    """token 数超阈值时截断尾部, 保留前 N token."""

    def __init__(self, max_tokens: int = 500) -> None:
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "truncating"

    def process(
        self,
        tool_name: str,
        original_content: str,
        token_budget: int,
        meter: TokenMeter,
    ) -> str:
        if not original_content:
            return original_content
        effective_max = min(self._max_tokens, token_budget) if token_budget > 0 else self._max_tokens
        if meter.count_text(original_content) <= effective_max:
            return original_content
        # 字符级近似截断 (token 数 × ~3 倍字符, 给 buffer 让 footer 装得下)
        approx_chars = effective_max * 3
        truncated = original_content[:approx_chars].rstrip()
        return (
            f"{truncated}\n\n"
            f"[... {tool_name} 输出过长已截断, 完整结果已处理]"
        )
