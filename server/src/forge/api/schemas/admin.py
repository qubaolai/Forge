"""管理后台 API 的 request / response schema。"""

from typing import Any

from pydantic import BaseModel, Field


# ---- 模型 ----
class ModelOut(BaseModel):
    model_id: str
    name: str
    display_name: str = ""
    model_type: str = "text"
    is_enabled: bool = True
    is_default: bool = False
    priority: int = 0
    cost_tier: str = "mid"


class ModelToggleIn(BaseModel):
    enabled: bool


class ModelSetDefaultIn(BaseModel):
    pass  # 无需额外参数


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
