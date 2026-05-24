"""用户额度配置.

这里的额度模型对齐 OpenAI / Claude Code 这类产品常见做法:
按真实模型用量进入滚动窗口, 而不是按 agent 运行墙钟时间扣减。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class UsageQuotaWindowSettings(BaseModel):
    """一个滚动用量窗口.

    至少配置一个 limit 才会产生阻断效果。多个 limit 同时配置时,
    任意一个超出都会阻断后续 LLM 调用。
    """

    model_config = {"extra": "forbid"}

    window_seconds: int
    limit_usd: float | None = None
    limit_tokens: int | None = None
    limit_calls: int | None = None

    @field_validator("window_seconds")
    @classmethod
    def _positive_window_seconds(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("window_seconds 必须大于 0")
        return int(value)

    @field_validator("limit_usd", "limit_tokens", "limit_calls", mode="before")
    @classmethod
    def _empty_limit_to_none(cls, value: Any) -> Any:
        if value == "":
            return None
        return value

    @field_validator("limit_usd")
    @classmethod
    def _positive_limit_usd(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("limit_usd 必须大于 0")
        return value

    @field_validator("limit_tokens", "limit_calls")
    @classmethod
    def _positive_int_limits(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("limit_tokens / limit_calls 必须大于 0")
        return value

    @property
    def has_limits(self) -> bool:
        return any(
            limit is not None
            for limit in (self.limit_usd, self.limit_tokens, self.limit_calls)
        )


class UserQuotaSettings(BaseModel):
    """按用户统计的 LLM 用量额度."""

    model_config = {"extra": "forbid"}

    enabled: bool = True
    five_hour: UsageQuotaWindowSettings = Field(
        default_factory=lambda: UsageQuotaWindowSettings(window_seconds=5 * 60 * 60)
    )
    weekly: UsageQuotaWindowSettings = Field(
        default_factory=lambda: UsageQuotaWindowSettings(window_seconds=7 * 24 * 60 * 60)
    )
    alert_threshold: float = 0.8

    @field_validator("alert_threshold")
    @classmethod
    def _valid_alert_threshold(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError("alert_threshold 必须在 (0, 1] 范围内")
        return float(value)

    @property
    def has_limits(self) -> bool:
        return self.five_hour.has_limits or self.weekly.has_limits
