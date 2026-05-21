"""记忆系统配置: 摘要 LLM / 触发策略."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class MemorySummarizerSettings(BaseSettings):
    """摘要任务使用的 LLM. 走独立 provider/model, 不复用 chat 主模型.

    provider/model 必须在 llm.providers 下能找到 (会被 Settings.resolve_utility_llm 解析).
    留空则依次回落到 utility_llm → llm.default.
    """
    model_config = SettingsConfigDict(extra="ignore")

    provider: str = ""
    model: str = ""
    history_limit: int = 100
    max_summary_tokens: int = 1500


class MemoryTriggerSettings(BaseSettings):
    """什么时候派发摘要任务."""
    model_config = SettingsConfigDict(extra="ignore")

    every_n_turns: int = 10


class MemorySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    summarizer: MemorySummarizerSettings = MemorySummarizerSettings()
    trigger: MemoryTriggerSettings = MemoryTriggerSettings()
