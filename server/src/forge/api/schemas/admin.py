"""管理后台 API 的 request / response schema。"""

from typing import Any

from pydantic import BaseModel, Field


# ---- 模型 ----
class ModelOut(BaseModel):
    model_id: str
    name: str
    display_name: str = ""
    model_type: str = "text"
    context_window: int = 128000
    max_output_tokens: int = 4096
    supports_tools: bool = True
    supports_images: bool = False
    supports_thinking: bool = False
    thinking_options: list[str] | None = None
    extra_params: dict[str, Any] | None = None
    is_enabled: bool = True
    is_default: bool = False
    priority: int = 0
    cost_tier: str = "mid"


class ModelToggleIn(BaseModel):
    enabled: bool


class ModelSetDefaultIn(BaseModel):
    pass  # 无需额外参数


class ModelCreateIn(BaseModel):
    """管理端手动新增模型（归属某供应商）。"""

    name: str  # 模型名: gpt-4o / qwen-plus ...
    display_name: str = ""
    model_type: str = "text"  # text / embedding / reranker
    context_window: int = 128000
    max_output_tokens: int = 4096
    supports_tools: bool = True
    supports_images: bool = False
    supports_thinking: bool = False
    thinking_options: list[str] | None = None
    extra_params: dict[str, Any] | None = None
    cost_tier: str = "mid"  # cheap / mid / expensive
    priority: int = 0


class ModelUpdateIn(BaseModel):
    """管理端更新模型（全字段可选；含启停/设默认）。"""

    name: str | None = None
    display_name: str | None = None
    model_type: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool | None = None
    supports_images: bool | None = None
    supports_thinking: bool | None = None
    thinking_options: list[str] | None = None
    extra_params: dict[str, Any] | None = None
    cost_tier: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    is_default: bool | None = None


# ---- 供应商 ----
class ProviderOut(BaseModel):
    id: int
    provider_id: str
    name: str
    impl: str = ""
    base_url: str | None = None
    is_enabled: bool = True
    priority: int = 0
    routing_config: dict[str, Any] | None = None
    key_count: int = 0
    model_count: int = 0
    models: list[ModelOut] = Field(default_factory=list)


class ProviderToggleIn(BaseModel):
    enabled: bool


# ---- 供应商 API-Key ----
class ProviderKeyOut(BaseModel):
    """供应商 API-Key 脱敏视图（绝不返回明文/密文）。"""

    key_id: str
    key_fingerprint: str
    is_enabled: bool = True
    weight: int = 1
    cooldown_until: str | None = None
    failure_score: int = 0
    last_error_at: str | None = None


class ProviderKeyCreateIn(BaseModel):
    api_key: str  # 明文, 后端加密后落库
    weight: int = 1


class ProviderKeyUpdateIn(BaseModel):
    enabled: bool | None = None
    weight: int | None = None


# ---- 事件 ----
class ConfigChangeEvent(BaseModel):
    type: str  # provider_toggled / model_toggled / model_default_changed
    provider: str
    model: str | None = None
    model_type: str | None = None
    enabled: bool | None = None
    count: int | None = None
    timestamp: str


# ---- 通用响应 ----
class ToggleResultOut(BaseModel):
    provider: str | None = None
    model_id: str | None = None
    enabled: bool | None = None
    pool: dict[str, int] = Field(default_factory=dict)
    is_default: bool | None = None
