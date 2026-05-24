"""Reranker 工厂.

注册机制完全对齐 EmbedderFactory:
    - _REGISTRY 是 provider → Reranker 子类 的映射
    - 实现类用 @register_reranker("xxx") 装饰自己, import 时自动落表
    - 工厂的 create() 根据 provider 实例化对应类

模型网关入口 (build_reranker_from_settings):
    - 业务侧唯一入口. 启动期装配 1 次, 全生命周期复用.
    - 进程内单例 memo, 不需要复合 key 池.
    - Reranker 不做 provider 级 fallback: 失败由 ParentChildRetriever 内部
      降级到 fusion_score, 切换 provider 收益不大.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from .base import Reranker

if TYPE_CHECKING:
    from forge.config.settings import Settings

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
# 模型网关: 启动期装配, 进程内单例 memo
# ----------------------------------------------------------------------
_built: Reranker | None = None
_build_lock = threading.Lock()


def build_reranker_from_settings(settings: Settings) -> Reranker:
    """从全局 settings 构造 Reranker, 进程内 memo. 二次调用返回缓存."""
    global _built
    if _built is not None:
        return _built
    with _build_lock:
        if _built is not None:
            return _built
        cfg = settings.reranker
        _built = RerankerFactory.create(cfg.provider, cfg.providers[cfg.provider])
        return _built


def reset_reranker_cache() -> None:
    """清空进程内 reranker 缓存. 测试隔离用."""
    global _built
    _built = None


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
