"""聊天路径工具白名单 (走 agent_profiles).

chat 路径的工具集来源于 agent_profiles.profiles.chat.tools_allowed.
assembler 渲染 system_prompt 时用此函数列出 chat 模式可用工具.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from forge.agents.profiles import get_agent_profile
from forge.tools.base import Tool
from forge.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def resolve_chat_tools(settings=None) -> tuple[Tool, ...]:  # noqa: ARG001
    """按 chat profile 的 tools_allowed 过滤 ToolRegistry.

    settings 参数保留是为了兼容历史调用约定; 现版本不读它, profile loader
    自身依赖全局 Settings 单例.
    """
    try:
        profile = get_agent_profile("chat")
    except (ValueError, RuntimeError) as exc:
        # profile 未加载或未定义 chat mode: 返回空集合, 不抛错; assembler 仍能渲染.
        logger.warning("chat profile 未就绪 (%s), 不开放任何工具给对话", exc)
        return ()
    return filter_tools_by_name(ToolRegistry.get_all(), set(profile.tools_allowed))


def filter_tools_by_name(tools: Iterable[Tool], allowed_names: set[str]) -> tuple[Tool, ...]:
    if not allowed_names:
        return ()
    selected = tuple(t for t in tools if t.name in allowed_names)
    missing = sorted(allowed_names - {t.name for t in selected})
    if missing:
        logger.warning("chat profile.tools_allowed 含未注册工具: %s", missing)
    return selected


__all__ = ["filter_tools_by_name", "resolve_chat_tools"]
