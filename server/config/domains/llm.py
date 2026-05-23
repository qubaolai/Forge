"""LLM 层配置: provider 多模型多 key 切换 + 工具模型 + 调用解析."""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator


# ======================================================================
# ModelCapabilities — model 能力声明 (router 决策依据)
# ======================================================================
class ModelCapabilities(BaseModel):
    """单个 model 的能力清单.

    Router 用这些字段过滤候选 (例如 requires_tools=True 时只选 supports_tools=True 的).
    所有字段都有保守默认值; 未配置时按"全功能"的安全方向取舍.
    """

    model_config = {"extra": "ignore"}

    supports_tools: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True
    supports_thinking: bool = False
    context_window: int = 4096
    max_output_tokens: int = 4096
    cost_tier: Literal["cheap", "mid", "expensive"] = "mid"
    """cheap < $1/1M tokens, mid $1-10, expensive > $10. 给成本路由用."""


# ======================================================================
# ModelConfig
# ======================================================================
class ModelConfig(BaseModel):
    """单个 model 的元数据 + 推理类开关.

    thinking / reasoning_effort 由 provider 自行翻译成对应的 SDK 调用:
        - DeepSeek:  thinking → extra_body.thinking={type:enabled}
                     reasoning_effort → top-level, 取值 "high" | "max"
        - OpenAI o-series: 只用 reasoning_effort, 取值 "low" | "medium" | "high"
        - Anthropic: thinking → top-level thinking={type:enabled,budget_tokens:N}

    provider 不支持的字段会被忽略, 不会触发 SDK 报错.

    capabilities 字段供 Router 用. 不配置时走默认 (大多数 chat 模型成立).
    旧 yaml 里的 context_window 顶级字段会自动镜像到 capabilities.context_window,
    以保证向后兼容.
    """
    model_config = {"extra": "allow"}

    name: str
    display_name: str | None = None
    context_window: int | None = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    quota_controlled: bool | None = None
    """是否按服务端预置模型计入用户额度. None 表示继承 provider 配置."""

    @model_validator(mode="after")
    def _mirror_top_level_context_window(self) -> ModelConfig:
        """旧 yaml 在 ModelConfig 顶层写 context_window, 同步到 capabilities."""
        if self.context_window is not None:
            self.capabilities = self.capabilities.model_copy(
                update={"context_window": self.context_window}
            )
        return self


# ======================================================================
# LLMProviderConfig — 多 api_key + 策略选择
# ======================================================================
KeyStrategy = Literal["round_robin", "random", "first"]


class LLMProviderConfig(BaseModel):
    """单个 LLM provider 的配置.

    api_keys 支持多个: 用于流量分担 / 限流隔离 / 主备隔离.
    base_url / timeout 是 client 级参数 (建 SDK client 时用),
    与 api_key 一起决定 client 池化 key.

    impl: 工厂注册名. 不填默认等于 yaml 里的 provider 名.
    用途: 同一份实现类可以在 yaml 里建多个配置 profile.
    """
    model_config = {"extra": "allow"}

    # ---- key 配置 ----
    api_keys: list[str] = Field(default_factory=list)
    key_strategy: KeyStrategy = "round_robin"

    # ---- client 级参数 ----
    impl: str | None = None
    base_url: str | None = None
    timeout: float = 30.0
    quota_controlled: bool = False
    """仅服务端预置 provider 设为 True; 用户自带 provider/API key 不计用户额度."""

    # ---- model 配置 ----
    models: list[ModelConfig] = Field(min_length=1)
    default_params: dict[str, Any] = Field(default_factory=dict)

    _rr_counter: int = PrivateAttr(default=0)
    _rr_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @model_validator(mode="after")
    def _normalize(self) -> LLMProviderConfig:
        # 去重 + 过滤空 key
        seen: set[str] = set()
        normalized: list[str] = []
        for k in self.api_keys:
            k = (k or "").strip()
            if k and k not in seen:
                seen.add(k)
                normalized.append(k)
        self.api_keys = normalized

        # model name 唯一性
        names = [m.name for m in self.models]
        if len(names) != len(set(names)):
            raise ValueError(f"models 中存在重名: {names}")
        return self

    def default_model_name(self) -> str:
        return self.models[0].name

    def find_model(self, name: str) -> ModelConfig:
        for m in self.models:
            if m.name == name:
                return m
        raise ValueError(
            f"model={name!r} 不在 provider 配置中, "
            f"可选: {[m.name for m in self.models]}"
        )

    def select_api_key(self) -> str:
        """按策略选取一个 api_key. 池化层据此确定 client 实例."""
        if not self.api_keys:
            raise ValueError("provider 未配置任何 api_key, 无法 select_api_key")
        if len(self.api_keys) == 1 or self.key_strategy == "first":
            return self.api_keys[0]
        if self.key_strategy == "random":
            return random.choice(self.api_keys)
        # round_robin (默认)
        with self._rr_lock:
            idx = self._rr_counter % len(self.api_keys)
            self._rr_counter += 1
            return self.api_keys[idx]


# ======================================================================
# LLMCallSpec — 一次调用的完整解析结果
# ======================================================================
# 这些字段会作为 chat 方法的命名参数, 不放进 extra
_STD_CALL_FIELDS = frozenset({
    "temperature", "max_tokens", "top_p",
    "thinking", "reasoning_effort", "thinking_budget", "top_k",
})


@dataclass(frozen=True)
class LLMCallSpec:
    """单次 LLM 调用的解析结果.

    分两部分:
        - 池化身份: (impl, api_key) 决定用哪个 SDK client 实例
        - 调用参数: model + 各种 per-call 超参, chat 方法的 kwargs
    """
    impl: str
    api_key: str
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    base_url: str | None = None
    timeout: float = 30.0
    quota_controlled: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def client_key(self) -> tuple[str, str]:
        """池化 key: (impl, api_key)."""
        return (self.impl, self.api_key)

    @property
    def client_options(self) -> dict[str, Any]:
        """建 SDK client 时用的参数."""
        return {"base_url": self.base_url, "timeout": self.timeout}

    @property
    def call_kwargs(self) -> dict[str, Any]:
        """传给 chat 方法的命名参数 (model + 推理超参).

        provider 不认识的字段通过 **spec.extra 单独传入.
        """
        kw: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "thinking": self.thinking,
            "reasoning_effort": self.reasoning_effort,
            "thinking_budget": self.thinking_budget,
            "top_k": self.top_k,
        }
        return kw


# ======================================================================
# LLMConfig
# ======================================================================
class BudgetSettings(BaseModel):
    """LLM 调用预算配置. 全 None 表示不限制 (向后兼容).

    优先级 (高 → 低):
        1. user_daily_limits_usd[user_id]   — 该用户显式上限
        2. default_user_daily_limit_usd     — 所有用户的默认上限
        3. global_daily_limit_usd           — 全局所有调用的总上限

    超出抛 LLMBudgetExceeded, retry / fallback 都不救场.
    """
    model_config = {"extra": "forbid"}

    user_daily_limits_usd: dict[str, float] = Field(default_factory=dict)
    default_user_daily_limit_usd: float | None = None
    global_daily_limit_usd: float | None = None
    alert_threshold: float = 0.8

    @model_validator(mode="before")
    @classmethod
    def _coerce_empty_string_to_none(cls, data):
        """yaml 的 ${VAR:} 占位符在缺失时是空串, 这里转 None 给 float | None 字段."""
        if not isinstance(data, dict):
            return data
        for key in ("default_user_daily_limit_usd", "global_daily_limit_usd"):
            if data.get(key) == "":
                data[key] = None
        return data


class LLMConfig(BaseModel):
    """LLM 段配置: 多 provider, 每个 provider 下多个 model + 多 api_key."""
    model_config = {"extra": "forbid"}

    provider: str = ""
    default_model: str | None = None
    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    fallback_chain: str = ""
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    budget: BudgetSettings = Field(default_factory=BudgetSettings)

    @model_validator(mode="after")
    def _check_default(self) -> LLMConfig:
        if self.providers:
            if self.provider not in self.providers:
                raise ValueError(
                    f"llm.provider={self.provider!r} 未在 providers 中声明, "
                    f"可用: {sorted(self.providers.keys())}"
                )
            if self.default_model is not None:
                self.providers[self.provider].find_model(self.default_model)
        return self

    def fallback_pairs(self) -> list[tuple[str, str]]:
        """解析 fallback_chain 字符串为 (provider, model) 列表."""
        if not self.fallback_chain.strip():
            return []
        pairs: list[tuple[str, str]] = []
        for token in self.fallback_chain.split(","):
            token = token.strip()
            if not token or ":" not in token:
                continue
            prov, model = token.split(":", 1)
            pairs.append((prov.strip(), model.strip()))
        return pairs

    def resolve(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> LLMCallSpec:
        """根据 provider + model 解析一份完整调用规约.

        合并规则 (优先级低 → 高):
            1. provider.default_params (provider 级默认)
            2. model 级字段 (extra='allow' 透传 + 显式字段)
            3. 必填: impl / api_key / model / base_url / timeout

        api_key 按 provider.key_strategy 在已配置的 api_keys 中选取.
        """
        target_provider = provider or self.provider
        if target_provider not in self.providers:
            raise ValueError(
                f"未配置的 LLM provider: {target_provider!r}, "
                f"可用: {sorted(self.providers.keys())}"
            )

        pcfg = self.providers[target_provider]
        impl = pcfg.impl or target_provider

        if model is not None:
            target_model = model
            pcfg.find_model(target_model)
        elif target_provider == self.provider and self.default_model:
            target_model = self.default_model
        else:
            target_model = pcfg.default_model_name()
        mcfg = pcfg.find_model(target_model)
        quota_controlled = (
            mcfg.quota_controlled
            if mcfg.quota_controlled is not None
            else pcfg.quota_controlled
        )

        # provider 默认参数 + model 级覆盖
        merged: dict[str, Any] = dict(pcfg.default_params)
        model_dump = mcfg.model_dump(
            exclude={"name", "display_name", "context_window", "quota_controlled"},
            exclude_none=True,
        )
        merged.update(model_dump)

        # 拆出标准字段, 剩下走 extra (provider 特有透传)
        extra = {k: v for k, v in merged.items() if k not in _STD_CALL_FIELDS}

        return LLMCallSpec(
            impl=impl,
            api_key=pcfg.select_api_key(),
            model=target_model,
            temperature=merged.get("temperature"),
            max_tokens=merged.get("max_tokens"),
            top_p=merged.get("top_p"),
            base_url=pcfg.base_url,
            timeout=pcfg.timeout,
            quota_controlled=quota_controlled,
            extra=extra,
        )

    def list_providers(self) -> list[str]:
        return sorted(self.providers.keys())

    def list_models(self, provider: str | None = None) -> list[ModelConfig]:
        target = provider or self.provider
        if target not in self.providers:
            raise ValueError(f"未配置的 provider: {target!r}")
        return self.providers[target].models

    def effective_default_model(self, provider: str | None = None) -> str:
        """获取 (默认或指定) provider 的默认 model 名."""
        target = provider or self.provider
        if target == self.provider and self.default_model:
            return self.default_model
        return self.providers[target].default_model_name()


# ======================================================================
# UtilityLLMConfig — 轻量任务公共模型
# ======================================================================
class UtilityLLMConfig(BaseModel):
    """工具模型配置 — 供标题生成、摘要、意图识别等轻量任务共用.

    三级回落 (由 Settings.resolve_utility_llm 实现):
        任务专属 provider/model → utility_llm → llm.default
    """
    model_config = {"extra": "forbid"}

    provider: str = ""
    model: str = ""
