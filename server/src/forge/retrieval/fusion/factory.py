"""Fusion 与 Aggregator 工厂.

两者是正交的策略维度, 各自独立工厂, 不做笛卡尔积合并.
注册机制对齐 EmbedderFactory.
"""

from __future__ import annotations

from .base import Aggregator, Fusion

# ======================================================================
# Fusion
# ======================================================================
_FUSION_REGISTRY: dict[str, type[Fusion]] = {}


def register_fusion(strategy: str):
    def decorator(cls: type[Fusion]) -> type[Fusion]:
        if not issubclass(cls, Fusion):
            raise TypeError(f"@register_fusion 只能装饰 Fusion 子类, 收到 {cls.__name__}")
        if strategy in _FUSION_REGISTRY:
            raise ValueError(
                f"Fusion 重复注册: {strategy} "
                f"(已存在: {_FUSION_REGISTRY[strategy].__name__}, 新增: {cls.__name__})"
            )
        _FUSION_REGISTRY[strategy] = cls
        return cls

    return decorator


class FusionFactory:
    """Fusion 工厂入口."""

    @staticmethod
    def create(strategy: str, config: dict | None = None) -> Fusion:
        if strategy not in _FUSION_REGISTRY:
            raise ValueError(
                f"未注册的 fusion strategy: {strategy!r}. 已注册: {sorted(_FUSION_REGISTRY.keys())}"
            )
        return _FUSION_REGISTRY[strategy](config or {})

    @staticmethod
    def list_strategies() -> list[str]:
        return sorted(_FUSION_REGISTRY.keys())


# ======================================================================
# Aggregator
# ======================================================================
_AGG_REGISTRY: dict[str, type[Aggregator]] = {}


def register_aggregator(name: str):
    def decorator(cls: type[Aggregator]) -> type[Aggregator]:
        if not issubclass(cls, Aggregator):
            raise TypeError(f"@register_aggregator 只能装饰 Aggregator 子类, 收到 {cls.__name__}")
        if name in _AGG_REGISTRY:
            raise ValueError(
                f"Aggregator 重复注册: {name} "
                f"(已存在: {_AGG_REGISTRY[name].__name__}, 新增: {cls.__name__})"
            )
        _AGG_REGISTRY[name] = cls
        return cls

    return decorator


class AggregatorFactory:
    """Aggregator 工厂入口."""

    @staticmethod
    def create(name: str, config: dict | None = None) -> Aggregator:
        if name not in _AGG_REGISTRY:
            raise ValueError(
                f"未注册的 aggregator: {name!r}. 已注册: {sorted(_AGG_REGISTRY.keys())}"
            )
        return _AGG_REGISTRY[name](config or {})

    @staticmethod
    def list_aggregators() -> list[str]:
        return sorted(_AGG_REGISTRY.keys())


# ======================================================================
# 自动加载
# ======================================================================
def _autoload() -> None:
    from . import (
        aggregator,  # noqa: F401
        rrf,  # noqa: F401
        weighted,  # noqa: F401
    )


_autoload()
