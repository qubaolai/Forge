"""LLM 调用合规审计日志.

为什么不写 DB:
    - LLM 调用频率高 (单 turn 多次 step + 多 tool round), 落库 = 千万级行/日,
      管理后台查询性能堪忧.
    - 结构化日志 (一行 JSON, 含 trace_id / user_id / token / cost 等) 走
      Loki / ELK 索引, 远比 DB 表查询更适合"过去 7 天该用户全部调用记录" 场景.
    - 日志聚合层已经在系统里 (observability/logging/logger.py 的 JSONFormatter).

记录字段:
    - request_id (= trace_id)
    - user_id (= ContextVar)
    - session_id (上层显式传入, 可选)
    - provider / model / api_key_fingerprint
    - prompt_tokens / completion_tokens / total_tokens
    - estimated_cost_usd
    - latency_ms
    - cache_hit / cache_type (Phase 3 接入后启用)
    - circuit_breaker_skipped
    - error 类别和信息

调用方式:
    - 业务层: get_audit_logger().log(LLMCallAuditRecord(...))
    - 同步, 直接走 logging, 零异步开销, 不阻塞调用路径
    - logging handler 自带异步缓冲 (StreamHandler -> stdout -> 日志采集器)
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from forge.core.request_context import (
    current_trace_id,
    current_user_id,
)

logger = logging.getLogger("llm.audit")


@dataclass(frozen=True)
class LLMCallAuditRecord:
    """单次 LLM provider 调用的审计记录.

    trace_id / user_id 留 None 表示走 ContextVar; 显式传值则覆盖.
    """

    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: float = 0.0
    api_key_fingerprint: str = "-"
    session_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    cache_hit: bool = False
    cache_type: str | None = None
    cached_tokens: int = 0
    """provider 端 prompt cache 命中的 token 数 (Anthropic ephemeral / OpenAI 自动)."""
    circuit_breaker_skipped: bool = False
    fallback_position: int = 0
    """0 = primary 命中; ≥1 = fallback 位置."""

    finish_reason: str | None = None
    error: str | None = None
    """非空表示本次调用最终失败 (含基础设施异常和业务异常)."""


class AuditLogger:
    """LLM 调用审计写出器, 全局单例.

    实现极简: 把 record 转 dict, 走 logger.info, 让 JSONFormatter 序列化.
    record 字段会作为 logging extra 透传, 在 JSON 日志里展开成顶级字段.
    """

    def log(self, record: LLMCallAuditRecord) -> None:
        payload = asdict(record)
        # trace_id / user_id 默认走 ContextVar, 显式传入时覆盖
        if not payload.get("trace_id"):
            payload["trace_id"] = current_trace_id() or "-"
        if not payload.get("user_id"):
            payload["user_id"] = current_user_id() or "-"

        # logging 自带的字段, 显式去重避免冲突
        extra = {f"audit_{k}": v for k, v in payload.items() if k not in {"trace_id", "user_id"}}
        # 把 trace_id / user_id 作为 LogRecord 内置字段 (TraceIdLogFilter 已留位)
        extra["trace_id"] = payload["trace_id"]
        extra["user_id"] = payload["user_id"]
        extra["audit_event"] = "llm.call"

        level = logging.WARNING if record.error else logging.INFO
        logger.log(
            level,
            "LLM 调用审计 provider=%s model=%s tokens=%d+%d cost=$%.6f latency=%.0fms err=%s",
            record.provider,
            record.model,
            record.prompt_tokens,
            record.completion_tokens,
            record.estimated_cost_usd,
            record.latency_ms,
            record.error or "-",
            extra=extra,
        )


_audit_logger = AuditLogger()


def get_audit_logger() -> AuditLogger:
    return _audit_logger
