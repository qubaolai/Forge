"""Tool 注册中心.

全局单例 + 装饰器风格注册. Agent 启动时拿到一份只读快照.

用法:
    @register_tool
    class Calculator(Tool):
        name = "calculator"
        ...

    tools = ToolRegistry.get_all()
"""

from __future__ import annotations

import logging
import threading

from .base import Tool

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Tool] = {}
# 注册时同步预计算每个工具的 schema, 避免每次取用都重算 (Tool 无状态, schema 永不变).
_OPENAI_SCHEMA_CACHE: dict[str, dict] = {}
_lock = threading.Lock()


def register_tool(cls: type[Tool]) -> type[Tool]:
    """类装饰器, 实例化并注册一份 (Tool 通常无状态, 单例即可)."""
    if not issubclass(cls, Tool):
        raise TypeError(f"@register_tool 只能装饰 Tool 子类, 收到 {cls.__name__}")
    instance = cls()
    if not getattr(instance, "name", None):
        raise ValueError(f"Tool {cls.__name__} 必须设置 name 字段")
    with _lock:
        if instance.name in _REGISTRY:
            raise ValueError(f"Tool 重复注册: {instance.name}")
        _REGISTRY[instance.name] = instance
        # 预计算 schema (注册期只跑一次, 业务调用 openai_schemas() 直接读 dict)
        _OPENAI_SCHEMA_CACHE[instance.name] = instance.openai_schema()
    logger.debug("Tool 已注册: %s", instance.name)
    return cls


class ToolRegistry:
    """工具查询入口."""

    @staticmethod
    def get(name: str) -> Tool | None:
        return _REGISTRY.get(name)

    @staticmethod
    def get_all() -> list[Tool]:
        return list(_REGISTRY.values())

    @staticmethod
    def names() -> list[str]:
        return sorted(_REGISTRY.keys())

    @staticmethod
    def openai_schemas() -> list[dict]:
        """所有工具的 schema 列表, 用于喂给 chat.completions(tools=...).

        ⚠️ 当前格式仅符合 **OpenAI function calling 规范** (含 deepseek /
        dashscope-compat 等 OpenAI 兼容协议). Anthropic / Gemini 等其他厂商
        格式不同 (Anthropic 用 input_schema, Gemini 用 FunctionDeclaration 等).

        当未来接入这些 provider 时, 由 provider 适配层在 chat_with_tools 内部
        将 OpenAI dict 转换成各自的原生格式, 调用方 (ReActAgent) 不感知差异.

        实现说明: 列表由注册期预计算的 _OPENAI_SCHEMA_CACHE 直接组装, 不重复
        生成. 返回的是新 list 但 dict 是缓存对象的引用, 调用方不要 mutate
        """
        return list(_OPENAI_SCHEMA_CACHE.values())
