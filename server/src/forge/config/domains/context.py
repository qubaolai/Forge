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


class ContextSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    digest: ContextDigestSettings = ContextDigestSettings()
