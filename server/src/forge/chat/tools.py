"""聊天路径工具白名单。

纯聊天仅开放查询类工具，不开放文件系统、命令、Git 等写操作权限。
任务执行请走 POST /api/v1/runs。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from forge.tools.base import Tool
from forge.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def resolve_chat_tools(settings) -> tuple[Tool, ...]:
    """按配置解析纯聊天可用工具。空白名单表示不开放任何工具。"""
    task_cfg = getattr(settings, "task_execution", None)
    allowed_names = set(getattr(task_cfg, "chat_tool_allowlist", ["knowledge_search"]) or [])
    return filter_tools_by_name(ToolRegistry.get_all(), allowed_names)


def filter_tools_by_name(tools: Iterable[Tool], allowed_names: set[str]) -> tuple[Tool, ...]:
    if not allowed_names:
        return ()
    selected = tuple(t for t in tools if t.name in allowed_names)
    missing = sorted(allowed_names - {t.name for t in selected})
    if missing:
        logger.warning("chat_tool_allowlist 包含未注册工具: %s", missing)
    return selected
