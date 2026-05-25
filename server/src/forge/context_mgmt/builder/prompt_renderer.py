"""PromptRenderer: 渲染 system prompt 文本.

行为:
    - 若 ContextRequest.system_prompt_override 非空, 直接返回 (跳过模板渲染).
      用于 adaptive 路径 _build_task_system_prompt() 等动态生成场景.
    - 否则调 PromptRegistry.render(template, **vars).
      模板渲染失败会被 PromptRegistry 内部捕获并返回 fallback 文本.

注意: 业务数据 (tools / kb_list / user_name 等) 由调用方放入
ContextRequest.system_prompt_vars, PromptRenderer 不直接拉业务数据,
避免反向耦合.
"""

from __future__ import annotations

import logging

from forge.context_mgmt.types import ContextRequest
from forge.prompts import get_registry

logger = logging.getLogger(__name__)


class PromptRenderer:
    """无状态. 同一实例可并发处理多个请求."""

    async def render(self, request: ContextRequest) -> str:
        if request.system_prompt_override:
            return request.system_prompt_override
        try:
            return get_registry().render(
                request.system_prompt_template, **request.system_prompt_vars
            )
        except Exception as exc:  # noqa: BLE001
            # PromptRegistry.render 内部已有 fallback, 这里再兜一层保险
            logger.warning(
                "PromptRenderer 兜底 (registry 渲染失败): template=%s err=%s",
                request.system_prompt_template, exc,
            )
            return "你是一个 helpful 的 AI 助手. 请回答用户的最新一条消息."
