"""向量库工厂.

注册机制与 Embedder 一致, 见 embedding/factory.py 注释.
"""

from __future__ import annotations

from .base import ChildVectorStore

_REGISTRY: dict[str, type[ChildVectorStore]] = {}


def register_vector_store(backend: str):
    """类装饰器: 把 ChildVectorStore 子类登记到工厂.

    被装饰的类必须实现 __init__(self, config: dict).
    """

    def decorator(cls: type[ChildVectorStore]) -> type[ChildVectorStore]:
        if not issubclass(cls, ChildVectorStore):
            raise TypeError(
                f"@register_vector_store 只能装饰 ChildVectorStore 子类, 收到 {cls.__name__}"
            )
        if backend in _REGISTRY:
            raise ValueError(
                f"VectorStore backend 重复注册: {backend} "
                f"(已存在: {_REGISTRY[backend].__name__}, 新增: {cls.__name__})"
            )
        _REGISTRY[backend] = cls
        return cls

    return decorator


class VectorStoreFactory:
    """向量库工厂入口."""

    @staticmethod
    def create(backend: str, config: dict | None = None) -> ChildVectorStore:
        if backend not in _REGISTRY:
            raise ValueError(
                f"未注册的向量库 backend: {backend!r}. 已注册: {sorted(_REGISTRY.keys())}"
            )
        cls = _REGISTRY[backend]
        return cls(config or {})

    @staticmethod
    def list_backends() -> list[str]:
        return sorted(_REGISTRY.keys())


def _autoload() -> None:
    """import 内置实现, 触发自注册."""
    from forge.retrieval.stores.vector import chroma_store  # noqa: F401
    # 后续新增:
    # from . import milvus_store  # noqa: F401
    # from . import qdrant_store  # noqa: F401


_autoload()
