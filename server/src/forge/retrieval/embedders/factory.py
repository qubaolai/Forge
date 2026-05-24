"""Embedder 工厂.

注册机制:
    - _REGISTRY 是 provider → Embedder 子类 的映射
    - 实现类用 @register_embedder("xxx") 装饰自己, import 时自动落表
    - 工厂的 create() 根据 provider 实例化对应类

模型网关入口 (build_embedder_from_settings):
    - 业务侧唯一入口. 启动期装配 1 次, 全生命周期复用。
    - 进程内单例: settings 不可变, 装配结果可直接 memo. 不需要复合 key 池.
    - 测试隔离: reset_embedder_cache() 清空, 配合 reset_settings() 用.

新增 provider 的步骤:
    1. 写一个继承 Embedder 的类
    2. 加 @register_embedder("your_name") 装饰器
    3. 确保该模块被 import (见本文件底部的 _autoload)
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from .base import Embedder

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[Embedder]] = {}


def register_embedder(provider: str):
    """类装饰器: 把 Embedder 子类登记到工厂注册表."""

    def decorator(cls: type[Embedder]) -> type[Embedder]:
        if not issubclass(cls, Embedder):
            raise TypeError(f"@register_embedder 只能装饰 Embedder 子类, 收到 {cls.__name__}")
        if provider in _REGISTRY:
            raise ValueError(
                f"Embedder provider 重复注册: {provider} "
                f"(已存在: {_REGISTRY[provider].__name__}, 新增: {cls.__name__})"
            )
        _REGISTRY[provider] = cls
        return cls

    return decorator


class EmbedderFactory:
    """Embedder 工厂入口 (无状态, 每次 create 都新建实例)."""

    @staticmethod
    def create(provider: str, config: dict | None = None) -> Embedder:
        if provider not in _REGISTRY:
            raise ValueError(
                f"未注册的 embedding provider: {provider!r}. 已注册: {sorted(_REGISTRY.keys())}"
            )
        cls = _REGISTRY[provider]
        return cls(config or {})

    @staticmethod
    def list_providers() -> list[str]:
        return sorted(_REGISTRY.keys())


# ----------------------------------------------------------------------
# 模型网关: 启动期装配, 进程内单例 memo
# ----------------------------------------------------------------------
_built: Embedder | None = None
_build_lock = threading.Lock()


def build_embedder_from_settings(settings: Settings) -> Embedder:
    """从全局 settings 构造单一 Embedder，进程内 memo."""
    global _built
    if _built is not None:
        return _built

    with _build_lock:
        if _built is not None:
            return _built

        cfg = settings.embedding
        _built = EmbedderFactory.create(cfg.provider, cfg.providers[cfg.provider])
        return _built


def reset_embedder_cache() -> None:
    """清空进程内 embedder 缓存. 测试隔离用, 配合 reset_settings()."""
    global _built
    _built = None


def _autoload() -> None:
    """import 内置实现, 触发自注册.

    第三方 SDK 缺失 (例如 dashscope 没装) 时单独跳过, 不影响其他 embedder.
    """
    for mod_name in ("mock", "dashscope_embedder"):
        try:
            __import__(f"forge.retrieval.embedders.{mod_name}")
        except ImportError as e:
            logger.debug("embedder %s 未加载 (依赖缺失): %s", mod_name, e)
        except Exception as e:  # noqa: BLE001
            logger.warning("embedder %s 加载失败: %s", mod_name, e)


_autoload()
