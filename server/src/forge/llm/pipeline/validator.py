"""输入校验中间件.

职责:
    - 消息列表非空
    - 消息条数不超过上限 (防止天量历史 / 误传)
    - 单条消息内容大小不超过上限 (防止超大 prompt)
    - role 合法

非法时抛 InputValidationError, LLMGateway 转 4xx 响应.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..request import LLMRequest, LLMResponse
from .base import PreMiddleware

logger = logging.getLogger(__name__)


_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})


class InputValidationError(ValueError):
    """输入校验失败. 由 LLMGateway 上层映射为 4xx."""


@dataclass
class InputValidatorMiddleware(PreMiddleware):
    """基础输入校验. 防止非法/超大请求进入 LLM."""

    max_messages: int = 200
    """单次调用最多多少条消息."""

    max_content_chars: int = 200_000
    """单条消息 content 最大字符数 (粗粒度防御, 真实 token 数由后续 token_counter 把关)."""

    allow_empty_content: bool = True
    """允许空 content (某些 tool message 可能为空)."""

    async def process(self, req: LLMRequest) -> LLMResponse | None:
        msgs = req.messages
        if not msgs:
            raise InputValidationError("messages 不能为空")
        if len(msgs) > self.max_messages:
            raise InputValidationError(
                f"messages 数量超限: {len(msgs)} > {self.max_messages}"
            )
        for idx, m in enumerate(msgs):
            if m.role not in _ALLOWED_ROLES:
                raise InputValidationError(
                    f"messages[{idx}].role 非法: {m.role!r}, 允许 {sorted(_ALLOWED_ROLES)}"
                )
            content = m.content or ""
            if not self.allow_empty_content and not content.strip():
                raise InputValidationError(f"messages[{idx}].content 不能为空")
            if len(content) > self.max_content_chars:
                raise InputValidationError(
                    f"messages[{idx}].content 大小超限: {len(content)} > {self.max_content_chars}"
                )
        return None


__all__ = ["InputValidationError", "InputValidatorMiddleware"]
