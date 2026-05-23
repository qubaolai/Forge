"""模型目录 — 启动时从各供应商 API 获取模型列表 + 能力元数据合并 + 缓存。

启动流程:
    1. 从 providers 表加载已启用供应商
    2. 对每个供应商调用其 /models API
    3. 各供应商 adapter 归一化响应
    4. 合并内置能力元数据（context_window、supports_tools、thinking 等）
    5. 写内存缓存

任一供应商获取失败 → 服务启动失败。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 归一化模型元数据
# ---------------------------------------------------------------------------
@dataclass
class ThinkingMeta:
    type: Literal["reasoning_effort", "enabled"]
    options: list[str] | None = None
    default: str | None = None


@dataclass
class ModelInfo:
    name: str
    provider: str
    display_name: str = ""
    context_window: int = 128000
    supports_tools: bool = True
    supports_images: bool = False
    thinking: ThinkingMeta | None = None


# ---------------------------------------------------------------------------
# 内置能力元数据（API 不返回的关键能力）
# ---------------------------------------------------------------------------
_CAPABILITIES: dict[str, dict] = {
    # Anthropic
    "claude-sonnet-4-6":    {"context_window": 200000, "supports_tools": True,  "supports_images": True, "thinking": {"type": "enabled"}},
    "claude-sonnet-4-5":    {"context_window": 200000, "supports_tools": True,  "supports_images": True, "thinking": {"type": "enabled"}},
    "claude-haiku-4-5":     {"context_window": 200000, "supports_tools": True,  "supports_images": True, "thinking": {"type": "enabled"}},
    "claude-opus-4-7":      {"context_window": 200000, "supports_tools": True,  "supports_images": True, "thinking": {"type": "enabled"}},
    # OpenAI
    "gpt-4o":               {"context_window": 128000, "supports_tools": True,  "supports_images": True,  "thinking": None},
    "gpt-4o-mini":          {"context_window": 128000, "supports_tools": True,  "supports_images": True,  "thinking": None},
    # DeepSeek
    "deepseek-v4-pro":      {"context_window": 1000000, "supports_tools": True, "supports_images": False, "thinking": {"type": "reasoning_effort", "options": ["high", "max"], "default": "high"}},
    "deepseek-v4-flash":    {"context_window": 1000000, "supports_tools": True, "supports_images": False, "thinking": {"type": "reasoning_effort", "options": ["high", "max"], "default": "high"}},
    # DashScope (Qwen)
    "qwen3-max-preview":    {"context_window": 32768,  "supports_tools": True,  "supports_images": False, "thinking": None},
    "qwen-plus":            {"context_window": 131072, "supports_tools": True,  "supports_images": False, "thinking": None},
    "qwen3.6-plus":         {"context_window": 131072, "supports_tools": True,  "supports_images": False, "thinking": None},
    "qwen3.5-flash":        {"context_window": 8192,   "supports_tools": True,  "supports_images": False, "thinking": None},
    "qwen3.7-max":          {"context_window": 131072, "supports_tools": True,  "supports_images": False, "thinking": None},
}


# ---------------------------------------------------------------------------
# Model Catalog
# ---------------------------------------------------------------------------
class ModelCatalog:
    """全局单例，启动时从各供应商 API 拉取模型列表。"""

    def __init__(self) -> None:
        self._models: dict[str, list[ModelInfo]] = {}  # provider → models

    @property
    def models(self) -> dict[str, list[ModelInfo]]:
        return self._models

    def get_models(self, provider: str) -> list[ModelInfo]:
        return self._models.get(provider, [])

    def list_providers(self) -> list[str]:
        return sorted(self._models.keys())

    async def refresh(self, providers: list[dict]) -> None:
        """启动时调用：遍历供应商，从 API 获取模型列表。

        Args:
            providers: [{"name": "deepseek", "impl": "deepseek", "api_key": "sk-xxx", "base_url": None}, ...]
        """
        new_models: dict[str, list[ModelInfo]] = {}
        for p in providers:
            name = p["name"]
            try:
                models = await _fetch_models(p)
                new_models[name] = models
                logger.info("模型目录 %s: %d 个模型", name, len(models))
            except Exception:
                logger.exception("模型目录获取失败 provider=%s, 服务无法启动", name)
                raise
        self._models = new_models


# ---------------------------------------------------------------------------
# Per-provider fetching
# ---------------------------------------------------------------------------
async def _fetch_models(provider: dict) -> list[ModelInfo]:
    """调用供应商 API 获取模型列表，合并能力元数据。"""
    import os

    name = provider["name"]
    api_key = provider.get("api_key") or os.environ.get(f"{name.upper()}_API_KEY", "")
    base_url = provider.get("base_url")

    # 各供应商有不同 API 端点
    if name == "anthropic":
        raw_ids = _ANTHROPIC_MODELS
    elif name in ("deepseek", "openai", "dashscope"):
        raw_ids = await _fetch_openai_compatible_models(api_key, base_url, name)
    else:
        raise ValueError(f"不支持的供应商: {name}")

    models: list[ModelInfo] = []
    for model_id in raw_ids:
        cap = _CAPABILITIES.get(model_id, {})
        thinking_raw = cap.get("thinking")
        thinking = None
        if thinking_raw:
            thinking = ThinkingMeta(
                type=thinking_raw["type"],
                options=thinking_raw.get("options"),
                default=thinking_raw.get("default"),
            )
        models.append(ModelInfo(
            name=model_id,
            provider=name,
            display_name=cap.get("display_name", model_id),
            context_window=cap.get("context_window", 128000),
            supports_tools=cap.get("supports_tools", True),
            supports_images=cap.get("supports_images", False),
            thinking=thinking,
        ))
    return models


# Anthropic 无 /models API，使用内置列表
_ANTHROPIC_MODELS = [
    "claude-sonnet-4-6", "claude-sonnet-4-5",
    "claude-haiku-4-5", "claude-opus-4-7",
]


async def _fetch_openai_compatible_models(api_key: str, base_url: str | None, provider: str) -> list[str]:
    """调用 OpenAI-compatible /v1/models 端点获取模型 ID 列表。"""
    import httpx

    if not api_key:
        raise ValueError(f"{provider}: 缺少 API Key")

    url = f"{(base_url or '').rstrip('/')}/v1/models"
    if not url.startswith("http"):
        # 使用默认 base_url
        defaults = {
            "openai": "https://api.openai.com",
            "deepseek": "https://api.deepseek.com",
            "dashscope": "https://dashscope.aliyuncs.com/compatible-mode",
        }
        url = f"{defaults.get(provider, '')}/v1/models"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        data = resp.json()

    # OpenAI 格式: {"data": [{"id": "gpt-4o", ...}, ...]}
    items = data.get("data", [])
    return [item["id"] for item in items if "id" in item]


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_catalog: ModelCatalog | None = None


def get_model_catalog() -> ModelCatalog:
    if _catalog is None:
        raise RuntimeError("ModelCatalog 尚未初始化, 请确认 lifespan 已启动")
    return _catalog


def init_model_catalog() -> ModelCatalog:
    global _catalog
    _catalog = ModelCatalog()
    return _catalog
