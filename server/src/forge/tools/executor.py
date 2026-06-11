"""工具执行器.

职责:
    - 按 ToolCall 名称查工具
    - 用工具的 JSON Schema 校验 arguments
    - 调防护栏: rate_limiter (per-tool 限速)
    - 调用 Tool.run() / Tool.arun() 执行
    - 危险工具 (tool.dangerous=True) 写 audit JSONL (executed / blocked / failed)
    - 异常统一包装成 ToolError, 由调用方决定是否回灌给模型

同步 vs 异步入口:
    - execute(call):  同步入口, 调 tool.run(). 老路径 / 测试用.
    - aexecute(call): 异步入口, 优先 await tool.arun(); 没覆写 arun 的工具
      回退到 asyncio.to_thread(tool.run, ...). ReActAgent.stream 用这个.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from forge.core.types.errors import ToolNotFoundError, ToolValidationError
from forge.core.types.message import Message, ToolCall
from forge.guardrails.tool.rate_limiter import RateLimiter, get_rate_limiter

from .base import Tool
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutor:
    """执行 tool calls 并构造 tool 消息."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._registry = registry or ToolRegistry
        self._rate_limiter = rate_limiter or get_rate_limiter()

    def is_parallelism_safe(self, name: str) -> bool:
        tool = self._registry.get(name)
        if tool is None:
            return False
        return getattr(tool, "parallelism_safe", True)

    def execute(self, call: ToolCall, *, role: str = "local") -> Message:
        """同步入口. 新代码请用 aexecute()."""
        tool = self._registry.get(call.name)
        if tool is None:
            raise ToolNotFoundError(call.name, f"未注册的工具: {call.name}")

        args = dict(call.arguments)
        self._validate(tool, args)

        blocked = self._run_sync_guardrails(tool, role, args)
        if blocked is not None:
            self._audit_blocking(tool, role, args, blocked)
            return self._wrap_blocked_message(call, blocked)

        t0 = time.perf_counter()
        status = "ok"
        try:
            result = tool.run(args)
        except Exception as e:  # noqa: BLE001
            logger.exception("工具 %s 执行失败", call.name)
            content = f"[tool error] {e}"
            status = "error"
        else:
            content = self._serialize(result)

        self._log_exec(call, status, content, t0)
        self._audit_execution(tool, role, args, status, t0)
        return Message(
            role="tool",
            content=content,
            tool_call_id=call.id,
            name=call.name,
        )

    async def aexecute(self, call: ToolCall, *, role: str = "local") -> Message:
        """异步入口. 推荐用."""
        tool = self._registry.get(call.name)
        if tool is None:
            raise ToolNotFoundError(call.name, f"未注册的工具: {call.name}")

        args = dict(call.arguments)
        self._validate(tool, args)

        blocked = await self._run_async_guardrails(tool, role, args)
        if blocked is not None:
            await self._audit_blocking_async(tool, role, args, blocked)
            return self._wrap_blocked_message(call, blocked)

        t0 = time.perf_counter()
        status = "ok"
        try:
            if type(tool).arun is not Tool.arun:
                result = await tool.arun(args)
            else:
                result = await asyncio.to_thread(tool.run, args)
        except Exception as e:  # noqa: BLE001
            logger.exception("工具 %s 执行失败", call.name)
            content = f"[tool error] {e}"
            status = "error"
        else:
            content = self._serialize(result)

        self._log_exec(call, status, content, t0)
        await self._audit_execution_async(tool, role, args, status, t0)
        return Message(
            role="tool",
            content=content,
            tool_call_id=call.id,
            name=call.name,
        )

    # ------------------------------------------------------------------
    # 防护栏管线
    # ------------------------------------------------------------------
    def _run_sync_guardrails(self, tool: Tool, role: str, args: dict[str, Any]) -> str | None:
        # rate limiter 是 async; 同步入口跳过 (sync execute 主要给测试用)
        _ = (tool, role, args)
        return None

    async def _run_async_guardrails(
        self, tool: Tool, role: str, args: dict[str, Any]
    ) -> str | None:
        _ = args
        name = tool.name
        rl = await self._rate_limiter.check(name, role)
        if not rl.allow:
            logger.warning(
                "工具被限流拦截 name=%s role=%s reason=%s retry_after=%.2fs",
                name,
                role,
                rl.reason,
                rl.retry_after,
            )
            return f"{rl.reason}; retry_after={rl.retry_after:.2f}s"
        return None

    def _wrap_blocked_message(self, call: ToolCall, reason: str) -> Message:
        return Message(
            role="tool",
            content=f"[tool blocked] {reason}",
            tool_call_id=call.id,
            name=call.name,
        )

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    def _audit_blocking(self, tool: Tool, role: str, args: dict[str, Any], reason: str) -> None:
        if not getattr(tool, "dangerous", False):
            return
        try:
            asyncio.get_running_loop()
            return  # 异步上下文走 _audit_blocking_async
        except RuntimeError:
            pass
        asyncio.run(self._audit_blocking_async(tool, role, args, reason))

    async def _audit_blocking_async(
        self, tool: Tool, role: str, args: dict[str, Any], reason: str
    ) -> None:
        if not getattr(tool, "dangerous", False):
            return
        from forge.infrastructure.audit_log import AuditEntry, default_audit_log

        await default_audit_log().record(
            AuditEntry(
                tool=tool.name,
                outcome="blocked",
                args_summary=_summarize_args(tool, args),
                reason=reason,
                agent_role=role,
            )
        )

    def _audit_execution(
        self, tool: Tool, role: str, args: dict[str, Any], status: str, t0: float
    ) -> None:
        if not getattr(tool, "dangerous", False):
            return
        try:
            asyncio.get_running_loop()
            return
        except RuntimeError:
            pass
        asyncio.run(self._audit_execution_async(tool, role, args, status, t0))

    async def _audit_execution_async(
        self, tool: Tool, role: str, args: dict[str, Any], status: str, t0: float
    ) -> None:
        if not getattr(tool, "dangerous", False):
            return
        from forge.infrastructure.audit_log import AuditEntry, default_audit_log

        await default_audit_log().record(
            AuditEntry(
                tool=tool.name,
                outcome="executed" if status == "ok" else "failed",
                args_summary=_summarize_args(tool, args),
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                agent_role=role,
                reason=None if status == "ok" else "tool_runtime_error",
            )
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _log_exec(call: ToolCall, status: str, content: str, t0: float) -> None:
        from forge.core.request_context import current_client_type

        duration_ms = (time.perf_counter() - t0) * 1000
        args_preview = str(call.arguments)
        if len(args_preview) > 200:
            args_preview = args_preview[:200] + "...(truncated)"
        logger.info(
            "工具执行 name=%s status=%s duration_ms=%.1f result_len=%d client_type=%s args=%s",
            call.name,
            status,
            duration_ms,
            len(content or ""),
            current_client_type(),
            args_preview,
        )

    @staticmethod
    def _validate(tool, arguments: dict[str, Any]) -> None:
        schema = tool.parameters or {}
        required = schema.get("required", [])
        for r in required:
            if r not in arguments:
                raise ToolValidationError(tool.name, f"缺少必填参数 {r!r}")

        props = schema.get("properties", {})
        for k, v in arguments.items():
            if k not in props:
                continue
            expected = props[k].get("type")
            if expected and not _matches_type(v, expected):
                raise ToolValidationError(
                    tool.name,
                    f"参数 {k!r} 类型错误: 期望 {expected}, 实际 {type(v).__name__}",
                )

    @staticmethod
    def _serialize(result: Any) -> str:
        import json

        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            return str(result)

_TYPE_MAP: dict[str, type[Any] | tuple[type[Any], ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def _matches_type(value: Any, expected: str) -> bool:
    t = _TYPE_MAP.get(expected)
    if t is None:
        return True
    return isinstance(value, t)


def _summarize_args(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """裁剪 audit args: 只保留 tool.audit_payload_fields 显式声明的 key (值按 type 截断)."""
    declared = getattr(tool, "audit_payload_fields", ()) or ()
    out: dict[str, Any] = {}
    for k in declared:
        if k in args:
            out[k] = _safe_value(args[k])
    return out


def _safe_value(v: Any) -> Any:
    if isinstance(v, str):
        return _truncate(v, 200)
    return v


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "...(truncated)"


# ---------------------------------------------------------------------------
# 默认实例 (复用全局 ToolRegistry)
# ---------------------------------------------------------------------------
_default_executor: ToolExecutor | None = None


def get_default_executor() -> ToolExecutor:
    global _default_executor
    if _default_executor is None:
        _default_executor = ToolExecutor()
    return _default_executor


def reset_default_executor() -> None:
    """测试用 — 强制下次 get_default_executor 重建."""
    global _default_executor
    _default_executor = None
