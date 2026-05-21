"""BM25Store 工厂.

注册机制与 EmbedderFactory 一致 (装饰器自注册).

关键差异:
    - 工厂只接受 store 配置 + 调用方注入的 tokenizer 实例
    - tokenizer 不在工厂内部创建, 避免 "入库 tokenizer ≠ 查询 tokenizer" 的隐式 bug
    - 调用方 (装配代码) 负责保证全项目使用唯一 tokenizer 实例
"""

from __future__ import annotations

from forge.retrieval.common.tokenizer import Tokenizer

from .base import BM25Store

_REGISTRY: dict[str, type[BM25Store]] = {}


def register_bm25_store(provider: str):
    """类装饰器: 把 BM25Store 子类登记到工厂注册表."""

    def decorator(cls: type[BM25Store]) -> type[BM25Store]:
        if not issubclass(cls, BM25Store):
            raise TypeError(f"@register_bm25_store 只能装饰 BM25Store 子类, 收到 {cls.__name__}")
        if provider in _REGISTRY:
            raise ValueError(
                f"BM25Store provider 重复注册: {provider} "
                f"(已存在: {_REGISTRY[provider].__name__}, 新增: {cls.__name__})"
            )
        _REGISTRY[provider] = cls
        return cls

    return decorator


class BM25StoreFactory:
    """BM25Store 工厂入口."""

    @staticmethod
    def create(
        provider: str,
        config: dict,
        tokenizer: Tokenizer,
    ) -> BM25Store:
        """
        Args:
            provider:  已注册的 provider 名, 如 'sqlite_fts5'
            config:    传给具体实现的 store 子配置 (yaml 中 providers[provider] 段)
            tokenizer: 调用方创建并注入的 Tokenizer 实例
        """
        if provider not in _REGISTRY:
            raise ValueError(
                f"未注册的 BM25Store provider: {provider!r}. 已注册: {sorted(_REGISTRY.keys())}"
            )
        cls = _REGISTRY[provider]
        # 把 tokenizer 注入到 config 里, 由具体实现取用.
        # 用 dict 注入而不是改 __init__ 签名, 是为了让所有 BM25Store 子类
        # 保持 __init__(self, config: dict) 的统一签名 (与 Embedder 对齐).
        merged = dict(config)
        merged["_tokenizer"] = tokenizer
        return cls(merged)

    @staticmethod
    def list_providers() -> list[str]:
        return sorted(_REGISTRY.keys())


def _autoload() -> None:
    """import 内置实现, 触发自注册."""
    from . import sqlite_fts5  # noqa: F401


_autoload()
