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


class MemoryFactsSettings(BaseSettings):
    """用户长期事实层 (跨会话记忆): 抽取触发 / 召回 / 去重.

    默认关闭 (灰度); dev 环境在 sys_config.dev.yaml 显式打开.
    provider/model 留空走 utility 档位 (与 summarizer 同语义).
    """
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = False
    # 每 N 轮 (1 轮 = 1 user + 1 assistant = 2 条消息) 派发一次抽取任务
    extract_every_n_turns: int = 5
    # 召回 top_k 与相似度下限 (读路径)
    top_k: int = 5
    min_score: float = 0.5
    # 写入去重: 与已有事实相似度 >= 此阈值则跳过
    dedup_threshold: float = 0.92
    # 单次抽取最多落库多少条事实 (防 LLM 发散)
    max_facts_per_turn: int = 10
    provider: str = ""
    model: str = ""


class MemorySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    summarizer: MemorySummarizerSettings = MemorySummarizerSettings()
    trigger: MemoryTriggerSettings = MemoryTriggerSettings()
    facts: MemoryFactsSettings = MemoryFactsSettings()
