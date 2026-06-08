"""审计 Post 中间件 (网关级摘要).

与 dispatcher 内的 per-entry 详细审计不重复:
    - dispatcher._emit_audit: 每个 fallback entry 都记一条 (含 circuit_breaker_skipped /
      error 等内部信息), 用于排查链路问题
    - AuditMiddleware: 整个请求的最终结果摘要, 含 user_id / total_latency / cache_hit
      / fallback_position 等业务侧指标, 用于运营分析
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..request import LLMRequest, LLMResponse
from .base import PostMiddleware

logger = logging.getLogger(__name__)


@dataclass
class AuditMiddleware(PostMiddleware):
    """请求级摘要日志.

    输出 INFO 日志, 便于运营查看; 实际 Prometheus 指标 (per-call 维度) 仍由
    dispatcher 内部 _emit_audit 推送, 此处不重复.
    """

    log_level: int = logging.INFO

    async def process(self, req: LLMRequest, resp: LLMResponse) -> LLMResponse:
        logger.log(
            self.log_level,
            "LLM 请求完成: user=%s provider=%s model=%s "
            "task_type=%s cache_hit=%s fallback_pos=%d latency_ms=%.1f "
            "tokens=in:%s/out:%s finish=%s",
            req.user_id or "-",
            resp.provider or "-",
            resp.model or "-",
            req.task_type,
            resp.cache_hit,
            resp.fallback_position,
            resp.latency_ms,
            (resp.usage or {}).get("prompt_tokens")
            or (resp.usage or {}).get("input_tokens", "?"),
            (resp.usage or {}).get("completion_tokens")
            or (resp.usage or {}).get("output_tokens", "?"),
            resp.finish_reason or "-",
        )
        return resp


__all__ = ["AuditMiddleware"]
