"""Reranker 工厂.

注册机制完全对齐 EmbedderFactory:
    - _REGISTRY 是 provider → Reranker 子类 的映射
    - 实现类用 @register_reranker("xxx") 装饰自己, import 时自动落表
    - 工厂的 create() 根据 provider 实例化对应类

"""

from __future__ import annotations

import logging

from .base import Reranker

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[Reranker]] = {}


def register_reranker(provider: str):
    """类装饰器: 把 Reranker 子类登记到工厂注册表."""

    def decorator(cls: type[Reranker]) -> type[Reranker]:
        if not issubclass(cls, Reranker):
            raise TypeError(f"@register_reranker 只能装饰 Reranker 子类, 收到 {cls.__name__}")
        if provider in _REGISTRY:
            raise ValueError(
                f"Reranker provider 重复注册: {provider} "
                f"(已存在: {_REGISTRY[provider].__name__}, 新增: {cls.__name__})"
            )
        _REGISTRY[provider] = cls
        return cls

    return decorator


class RerankerFactory:
    """Reranker 工厂入口 (无状态, 每次 create 都新建实例)."""

    @staticmethod
    def create(provider: str, config: dict | None = None) -> Reranker:
        """
        Args:
            provider: 已注册的 provider 名, 如 'dashscope' / 'mock' / 'bge_local'
            config:   传给具体实现 __init__ 的配置 dict
        """
        if provider not in _REGISTRY:
            raise ValueError(
                f"未注册的 reranker provider: {provider!r}. 已注册: {sorted(_REGISTRY.keys())}"
            )
        cls = _REGISTRY[provider]
        return cls(config or {})

    @staticmethod
    def list_providers() -> list[str]:
        return sorted(_REGISTRY.keys())


# ----------------------------------------------------------------------
# 自动加载内置实现, 触发自注册装饰器
# ----------------------------------------------------------------------
def _autoload() -> None:
    from . import (
        dashscope_reranker,  # noqa: F401
        mock,  # noqa: F401
    )
    # 后续:
    # from . import bge_reranker        # noqa: F401


_autoload()
