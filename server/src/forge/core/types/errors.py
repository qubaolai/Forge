"""Agent / Tool / LLM 层错误体系.

业务层错误见 forge.core.exceptions (HTTP/业务码).
本文件定义内部组件之间用的异常类型, 不直接转 HTTP 错.
"""

from __future__ import annotations


class AgentPlatformError(Exception):
    """所有内部错误的根. 业务侧不应该直接 raise 这个."""


class ToolError(AgentPlatformError):
    """工具执行失败."""

    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"[tool={tool_name}] {message}")


class ToolNotFoundError(ToolError):
    """模型请求的工具未注册."""


class ToolValidationError(ToolError):
    """工具参数校验失败."""


class AgentMaxStepsError(AgentPlatformError):
    """Agent 达到最大迭代步数仍未给出最终答案."""

    def __init__(self, max_steps: int):
        self.max_steps = max_steps
        super().__init__(f"Agent 超过最大步数 {max_steps} 仍未结束")


class LLMError(AgentPlatformError):
    """LLM 调用失败的统一类型 (包装底层 SDK 异常)."""
