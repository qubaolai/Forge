"""Tool 系统入口.

业务侧:
    from forge.tools import Tool, ToolRegistry, register_tool, ToolExecutor

import 时触发 builtin 工具自注册.
"""

# 触发内置工具注册
from . import builtin  # noqa: F401
from .base import Tool
from .executor import ToolExecutor
from .registry import ToolRegistry, register_tool

__all__ = ["Tool", "ToolRegistry", "register_tool", "ToolExecutor"]
