"""TruncationStrategy 工厂."""

from __future__ import annotations

from .base import TruncationStrategy

_REGISTRY: dict[str, type[TruncationStrategy]] = {}


def register_truncation(strategy: str):
    def decorator(cls: type[TruncationStrategy]) -> type[TruncationStrategy]:
        if not issubclass(cls, TruncationStrategy):
            raise TypeError(
                f"@register_truncation 只能装饰 TruncationStrategy 子类, 收到 {cls.__name__}"
            )
        if strategy in _REGISTRY:
            raise ValueError(
                f"TruncationStrategy 重复注册: {strategy} "
                f"(已存在: {_REGISTRY[strategy].__name__}, 新增: {cls.__name__})"
            )
        _REGISTRY[strategy] = cls
        return cls

    return decorator


class TruncationFactory:
    """TruncationStrategy 工厂入口."""

    @staticmethod
    def create(strategy: str, config: dict | None = None) -> TruncationStrategy:
        if strategy not in _REGISTRY:
            raise ValueError(
                f"未注册的 truncation strategy: {strategy!r}. 已注册: {sorted(_REGISTRY.keys())}"
            )
        return _REGISTRY[strategy](config or {})

    @staticmethod
    def list_strategies() -> list[str]:
        return sorted(_REGISTRY.keys())


def _autoload() -> None:
    from . import tail  # noqa: F401
    # 后续:
    # from . import head_tail        # noqa: F401
    # from . import segment_topk     # noqa: F401


_autoload()
