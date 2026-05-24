"""记忆系统配置: 摘要 LLM / 触发策略."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class MemorySummarizerSettings(BaseSettings):
    """摘要任务使用的 LLM. 走独立 provider/model, 不复用 chat 主模型.

    provider/model 会被工具模型入口按 DB 缓存校验。留空则回落到任务主模型/主模型。
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
