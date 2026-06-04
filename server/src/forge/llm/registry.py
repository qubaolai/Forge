"""LLM Provider 注册表 + 工厂.

注册机制:
    @register_llm("xxx") class XxxLLM(LLM)        ── 类装饰器
    _autoload() 在 import 时触发各 provider 自注册

构造:
    build_llm_client(impl, api_key, client_options) -> LLM
    供 LLMClientPool 使用. 每个 (impl, api_key) 由池缓存复用.

辅助:
    list_providers()                ── 列出全部已注册 provider
    split_provider_model("p:m")     ── 拆分 "provider:model" 字符串
"""

from __future__ import annotations

import logging

from .providers.base import LLM

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[LLM]] = {}


def register_llm(provider: str):
    """类装饰器: 把 LLM 子类登记到工厂."""

    def decorator(cls: type[LLM]) -> type[LLM]:
        if not issubclass(cls, LLM):
            raise TypeError(f"@register_llm 只能装饰 LLM 子类, 收到 {cls.__name__}")
        if provider in _REGISTRY:
            raise ValueError(
                f"LLM provider 重复注册: {provider} "
                f"(已存在: {_REGISTRY[provider].__name__}, 新增: {cls.__name__})"
            )
        _REGISTRY[provider] = cls
        return cls

    return decorator


def list_providers() -> list[str]:
    """返回所有已注册的 provider 名."""
    return sorted(_REGISTRY.keys())


def build_llm_client(impl: str, api_key: str, client_options: dict | None = None) -> LLM:
    """工厂函数: 按 impl 名 + api_key + client 级参数构造 LLM client.

    给 LLMClientPool 注入用. client_options 支持 base_url / timeout.
    """
    if impl not in _REGISTRY:
        raise ValueError(f"未注册的 LLM provider: {impl!r}. 已注册: {list_providers()}")
    cls = _REGISTRY[impl]
    return cls(api_key, **(client_options or {}))


def split_provider_model(value: str | None) -> tuple[str | None, str | None]:
    """解析 "provider:model" 字符串. 缺失 provider 时返回 (None, model)."""
    raw = (value or "").strip()
    if not raw:
        return None, None
    if ":" not in raw:
        return None, raw
    provider, model = raw.split(":", 1)
    return provider.strip() or None, model.strip() or None


def _autoload() -> None:
    """import 内置实现, 触发自注册.

    每个 provider 单独 try/except: 外部 SDK 没装时, 该 provider 不可用,
    但不影响其他 provider 和整体 import.

    错误分级:
        - 外部 SDK 缺失 (`No module named '<sdk>'`) → DEBUG, 该 provider 不可用属合理
        - 内部模块 ImportError (循环导入 / forge.* 路径错误) → ERROR, 必须修复
        - 其他异常 → WARNING
    """
    log = logging.getLogger(__name__)
    for mod_name in ("openai", "anthropic", "google", "mock", "ollama"):
        try:
            __import__(f"forge.llm.providers.{mod_name}")
        except ImportError as e:
            msg = str(e)
            # 判断是否内部代码错误 (循环导入 / 改名后忘改 import 等)
            if "forge." in msg or "circular import" in msg or "partially initialized" in msg:
                log.error(
                    "LLM provider %s 内部 ImportError (循环导入或路径错误): %s",
                    mod_name, e,
                )
            else:
                log.debug("LLM provider %s 未加载 (依赖缺失): %s", mod_name, e)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM provider %s 加载失败: %s", mod_name, e)


_autoload()


__all__ = [
    "build_llm_client",
    "list_providers",
    "register_llm",
    "split_provider_model",
]
