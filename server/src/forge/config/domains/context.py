"""上下文管理配置: digest 引用化.

归属说明: digest 是 context 子系统 (短期、会话级), 与 memory (长期、跨会话) 解耦,
故配置独立成 context 段, 不并入 memory.digest。
"""
from __future__ import annotations

import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ContextDigestSettings(BaseSettings):
    """会话内容引用化 (digest) 配置."""
    model_config = SettingsConfigDict(extra="ignore")

    # 总开关。关闭后 HistoryProvider 行为与改动前完全一致 (不折叠任何消息)。
    enabled: bool = True
    # 读时单条消息触发折叠的 token 上限: 超过则替换为「引用占位 + digest/截断」。
    per_message_token_cap: int = 8000
    # 异步 digest 任务的最小处理阈值 (低于此值的消息不计算 digest)。
    min_tokens: int = 8000

    @model_validator(mode="after")
    def _clamp_min_tokens(self) -> ContextDigestSettings:
        """保证 min_tokens <= per_message_token_cap。

        否则处于 (cap, min_tokens] 之间的消息会被读时折叠却永远不被 digest,
        长期停在廉价截断降级 (digest_pending)。夹紧到 cap 消除该死区。
        """
        if self.per_message_token_cap > 0 and self.min_tokens > self.per_message_token_cap:
            logger.warning(
                "context.digest.min_tokens(%d) > per_message_token_cap(%d), "
                "已夹紧到 cap 以保证可折叠的消息都能被 digest",
                self.min_tokens, self.per_message_token_cap,
            )
            self.min_tokens = self.per_message_token_cap
        return self


class ContextSemanticRecallSettings(BaseSettings):
    """语义历史召回配置 (HybridFilter 的早期轮次按语义相似度过滤).

    默认关闭: 启用后每轮要对当前问题做一次 query embedding + 读消息向量缓存,
    有额外延迟/成本, 故 opt-in。关闭时 HybridFilter 走 NullScorer (= 仅近期锚点保留,
    等价 RecentFilter 行为), 与改动前完全一致。

    依赖: 需配置可用的 embedding provider (settings.embedding); 不可用时整体降级 RecentFilter。
    """
    model_config = SettingsConfigDict(extra="ignore")

    # 总开关 (默认关, opt-in)。
    enabled: bool = False
    # 早期轮次保留的相似度阈值 (低于此分的整轮剔除)。
    min_score: float = 0.6
    # 最近多少轮无条件保留 (保证对话连贯, 不受语义过滤影响)。
    anchor_turns: int = 3
    # 冷路径每次为多少条近期消息补算 embedding。
    scan_limit: int = 50


class ContextSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    digest: ContextDigestSettings = ContextDigestSettings()
    semantic_recall: ContextSemanticRecallSettings = ContextSemanticRecallSettings()
