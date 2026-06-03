"""管理后台 API 的 request / response schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---- 模型 ----
class ChatModelConfigIn(BaseModel):
    context_window: int = Field(default=128000, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    capabilities: list[str] = Field(default_factory=list)
    thinking_options: list[str] | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)


class EmbeddingModelConfigIn(BaseModel):
    dimension: int = Field(default=1024, ge=1)
    batch_size: int = Field(default=10, ge=1)
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    max_retries: int = Field(default=3, ge=0)
    retry_backoff: float = Field(default=1.0, ge=0)
    provider_options: dict[str, Any] = Field(default_factory=dict)


class RerankerModelConfigIn(BaseModel):
    timeout_seconds: float = Field(default=5.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    retry_backoff: float = Field(default=1.0, ge=0)
    truncation_strategy: str = "tail"
    max_doc_chars: int = Field(default=4000, ge=1)
    monitor_threshold: float = Field(default=0.1, ge=0, le=1)
    provider_options: dict[str, Any] = Field(default_factory=dict)


def validate_model_config(model_type: str, config: dict[str, Any]) -> dict[str, Any]:
    schema = {
        "chat": ChatModelConfigIn,
        "embedding": EmbeddingModelConfigIn,
        "reranker": RerankerModelConfigIn,
    }.get(model_type)
    if schema is None:
        raise ValueError(f"不支持的模型类型: {model_type}")
    return schema(**config).model_dump()


class ModelOut(BaseModel):
    id: str
    model_id: str
    provider_id: str
    name: str
    display_name: str = ""
    model_type: Literal["chat", "embedding", "reranker"] = "chat"
    config: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    priority: int = 0
    cost_tier: str = "mid"


class ModelToggleIn(BaseModel):
    enabled: bool


class ModelCreateIn(BaseModel):
    """管理端手动新增模型（归属某供应商）。"""

    name: str  # 模型名: gpt-4o / qwen-plus ...
    display_name: str = ""
    model_type: Literal["chat", "embedding", "reranker"] = "chat"
    config: dict[str, Any] = Field(default_factory=dict)
    cost_tier: str = "mid"  # cheap / mid / expensive
    priority: int = 0

    @model_validator(mode="after")
    def _validate_config(self) -> ModelCreateIn:
        self.config = validate_model_config(self.model_type, self.config)
        return self


class ModelUpdateIn(BaseModel):
    """管理端更新模型。模型身份字段不可修改。"""

    display_name: str | None = None
    config: dict[str, Any] | None = None
    cost_tier: str | None = None
    priority: int | None = None
    enabled: bool | None = None


# ---- 供应商 ----
class ProviderOut(BaseModel):
    id: str
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


class SystemModelBindingUpdateIn(BaseModel):
    model_id: str | None = None


# ---- 事件 ----
class ConfigChangeEvent(BaseModel):
    type: str  # provider_toggled / model_toggled / system_model_binding_changed
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
