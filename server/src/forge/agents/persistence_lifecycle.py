"""把 ReActAgent 的生命周期事件投影到通用 RunStore (JSONL).

仅 CLI 模式 (plan_exec / workflow) 用. chat 模式仍走 chat_messages DB.

写入策略:
    on_start          -> RunStore.append_event("run_started", {mode, goal})
                         (RunStore.create_run 内部已发了, 这里二次确认 mode)
    after_step        -> append_event("step_completed", {step_index, content,
                                       tool_calls(摘要), usage, finish_reason,
                                       duration_ms})
    on_tool_result    -> 大产物 (content 超阈值) 落 artifact, 返回替换 message
                         `[artifact:<id>] {summary}`  → 不再回灌大文本到 LLM 上下文
    on_complete       -> transition_status("completed")
    on_error          -> transition_status("failed", payload={error})

设计要点:
    - 持久化失败不阻断主流程 (异常吞掉打日志, 主 agent 继续推进)
    - artifact 摘要: 截前 200 字符, 保留 LLM 对结果的"印象"; 详细内容靠 artifact 工具读
    - tool_calls 摘要: 只存 id/name/arguments_preview, 不存大的 raw_result (避免 jsonl 膨胀)
"""

from __future__ import annotations

import logging
from typing import Any

from forge.agents.lifecycle import (
    RunContext,
    RunResult,
    StepContext,
    StepOutcome,
)
from forge.core.types.message import Message, ToolCall
from forge.infrastructure.run_store import RunStore
from forge.infrastructure.run_store_models import Artifact
from forge.utils.id_generator import new_id

logger = logging.getLogger(__name__)


_ARGUMENTS_PREVIEW_LIMIT = 200
_ARTIFACT_SUMMARY_LIMIT = 200


class RunStorePersistenceLifecycle:
    """把 lifecycle 事件落到 RunStore.

    Args:
        store:                          RunStore 实例 (per-workspace)
        run_id:                         create_run 时拿到的 run_id
        large_artifact_threshold_bytes: 工具结果超过此值落 artifact (默认 8KB);
                                        0 表示禁用大产物落盘.
    """

    def __init__(
        self,
        store: RunStore,
        run_id: str,
        *,
        large_artifact_threshold_bytes: int = 8192,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._threshold = max(0, int(large_artifact_threshold_bytes))

    # ------------------------------------------------------------------
    # lifecycle hooks
    # ------------------------------------------------------------------
    async def on_start(self, ctx: RunContext) -> None:
        # create_run 时已写过 run_started; 这里追加一条 lifecycle_started
        # 方便客户端从事件流里看到 lifecycle 真正 attach 的时机.
        try:
            await self._store.append_event(
                self._run_id,
                "lifecycle_attached",
                {
                    "mode": ctx.mode,
                    "user_id": ctx.user_id,
                    "session_id": ctx.session_id,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("persistence on_start 写事件失败 run=%s", self._run_id)

    async def after_step(self, step: StepContext, outcome: StepOutcome) -> None:
        try:
            await self._store.append_event(
                self._run_id,
                "step_completed",
                {
                    "step_index": outcome.step_index,
                    "content_len": len(outcome.content or ""),
                    "content_preview": (outcome.content or "")[:200],
                    "tool_calls": [
                        self._tool_call_summary(tc) for tc in outcome.tool_calls
                    ],
                    "usage": dict(outcome.usage or {}),
                    "finish_reason": outcome.finish_reason,
                    "duration_ms": round(outcome.duration_ms, 1),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "persistence after_step 写事件失败 run=%s step=%d",
                self._run_id, outcome.step_index,
            )

    async def on_tool_result(self, tc: ToolCall, msg: Message) -> Message | None:
        """大产物落 artifact, 回灌占位 message 给 LLM 节省 context."""
        content = msg.content or ""
        # 阈值=0 时禁用; 没超阈值时不改写
        if self._threshold == 0 or len(content.encode("utf-8")) <= self._threshold:
            return None

        artifact_id = new_id("art")
        try:
            artifact = Artifact(
                artifact_id=artifact_id,
                run_id=self._run_id,
                kind="tool_output",
                payload={
                    "tool_call_id": tc.id,
                    "tool_name": tc.name,
                    "arguments": tc.arguments,
                    "content": content,
                },
                metadata={
                    "content_bytes": len(content.encode("utf-8")),
                },
            )
            await self._store.save_artifact(artifact)
            await self._store.append_event(
                self._run_id,
                "artifact_created",
                {
                    "artifact_id": artifact_id,
                    "kind": "tool_output",
                    "tool_call_id": tc.id,
                    "tool_name": tc.name,
                    "content_bytes": artifact.metadata["content_bytes"],
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "persistence on_tool_result 落 artifact 失败 run=%s tc=%s",
                self._run_id, tc.id,
            )
            return None

        # 回灌占位: LLM 看到 "[artifact:<id>] {summary}" 知道有大产物但不读全文
        summary = self._summary_of(content)
        replacement = Message(
            role="tool",
            content=f"[artifact:{artifact_id}] {summary}",
            tool_call_id=tc.id,
            name=tc.name,
        )
        return replacement

    async def on_complete(self, result: RunResult) -> None:
        try:
            await self._store.transition_status(
                self._run_id,
                "completed",
                payload={
                    "finish_reason": result.finish_reason,
                    "content_len": len(result.content or ""),
                    "tool_calls_count": len(result.tool_calls or []),
                    "usage": dict(result.usage or {}),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "persistence on_complete 转 completed 失败 run=%s", self._run_id,
            )

    async def on_error(self, exc: BaseException, partial: RunResult) -> None:
        try:
            await self._store.transition_status(
                self._run_id,
                "failed",
                payload={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "content_len": len(partial.content or ""),
                    "usage": dict(partial.usage or {}),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "persistence on_error 转 failed 失败 run=%s", self._run_id,
            )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _tool_call_summary(tc: ToolCall) -> dict[str, Any]:
        args_text = ""
        try:
            import json
            args_text = json.dumps(tc.arguments, ensure_ascii=False)[
                :_ARGUMENTS_PREVIEW_LIMIT
            ]
        except Exception:  # noqa: BLE001
            args_text = str(tc.arguments)[:_ARGUMENTS_PREVIEW_LIMIT]
        return {
            "id": tc.id,
            "name": tc.name,
            "arguments_preview": args_text,
        }

    @staticmethod
    def _summary_of(content: str) -> str:
        text = content.strip()
        if len(text) <= _ARTIFACT_SUMMARY_LIMIT:
            return text
        return text[:_ARTIFACT_SUMMARY_LIMIT] + "..."


__all__ = ["RunStorePersistenceLifecycle"]
