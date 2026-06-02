"""入站限流 Pre 中间件.

与 dispatcher 的"出站" 429 处理不同:
    - 入站限流: 防止单用户在网关层就发太多请求, 在 LLM 调用前拦截
    - 出站 429: 已经发到 provider 才被限流, 触发 Key 冷却 + 切下一个 Key
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..inbound_rate_limiter import (
    InboundRateLimiter,
    InboundRateLimitExceeded,
    get_inbound_rate_limiter,
)
from ..request import LLMRequest, LLMResponse
from .base import PreMiddleware

logger = logging.getLogger(__name__)


@dataclass
class InboundRateLimitMiddleware(PreMiddleware):
    """per-user RPM/TPM 滑动窗口限流.

    超额时抛 InboundRateLimitExceeded, LLMGateway 上层转 429 响应.
    """

    limiter: InboundRateLimiter | None = None

    def _resolve(self) -> InboundRateLimiter:
        return self.limiter or get_inbound_rate_limiter()

    async def process(self, req: LLMRequest) -> LLMResponse | None:
        limiter = self._resolve()
        if not limiter.enabled:
            return None
        uid = req.user_id or ""
        result = await limiter.check(uid, estimated_tokens=req.estimated_input_tokens)
        if not result.allow:
            logger.warning(
                "入站限流触发: user=%s reason=%s retry_after=%.1fs",
                uid or "-",
                result.reason,
                result.retry_after,
            )
            raise InboundRateLimitExceeded(result.reason, retry_after=result.retry_after)
        return None


__all__ = ["InboundRateLimitMiddleware"]
