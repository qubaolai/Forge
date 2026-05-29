"""Chat 路径下的模型元信息查询.

目前只暴露 `resolve_context_window`: 给定 model_options (provider + model),
从 ModelConfigCache 反查实际上下文窗口; 任何一步失败都回落到 default.

放在 chat 包下而非 llm 包下, 是因为 chat 路径需要在 TurnContext 构造时
就拿到 window 来决定压缩阈值, 而这是 chat 业务的 concern.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_WINDOW = 128_000


def _coerce_int(value: Any) -> int | None:
    """安全地把 cache dict 里的 context_window 转 int."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            v = int(value)
            return v if v > 0 else None
        except ValueError:
            return None
    return None


async def resolve_context_window(
    model_options: dict[str, Any] | None,
    *,
    default: int = DEFAULT_CONTEXT_WINDOW,
) -> int:
    """从 model_options 反查 context_window. 任何失败回落到 default.

    - model_options 缺失 / 缺 provider/model → default
    - cache 不可用 / 查不到该模型 → default
    - 拿到的字段不是正整数 → default
    """
    if not isinstance(model_options, dict):
        return default
    provider = model_options.get("provider")
    model = model_options.get("model")
    if not isinstance(provider, str) or not isinstance(model, str):
        return default

    try:
        from forge.llm.model_config_cache import ModelConfigCache  # noqa: PLC0415

        cache = ModelConfigCache.get_global()
        detail = await cache.get_model_detail(provider, model)
    except Exception:  # noqa: BLE001
        logger.warning(
            "查询 context_window 失败 provider=%s model=%s, 回落到 default=%d",
            provider, model, default,
        )
        return default

    if not isinstance(detail, dict):
        return default

    window = _coerce_int(detail.get("context_window"))
    if window is None:
        return default
    return window


__all__ = ["resolve_context_window", "DEFAULT_CONTEXT_WINDOW"]
