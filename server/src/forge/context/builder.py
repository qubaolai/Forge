"""CompositeContextBuilder: 唯一的 ContextBuilder 实现.

合成顺序 (统一塞进单条 system message):

    system:
        <base_prompt>

        ## 关于用户 (长期记忆)
        - ...

        ## 早期对话摘要
        <summary>

    + history (按 token 预算从最新往前累加)
    + 当前用户消息

降级行为:
    - 历史查询失败          -> 直接抛 (没历史不能假装继续)
    - 摘要 / 事实读取失败    -> 跳过, 记入 BuildMeta.degraded
    - 历史超 token 预算     -> 丢早期, 记 history_messages_dropped
"""

from __future__ import annotations

import asyncio
import logging

from forge.context.base import (
    AssembledContext,
    BuildMeta,
    BuildRequest,
    WorkflowContextLayer,
    WorkspaceContextLayer,
)
from forge.core.types.message import Message
from forge.infrastructure.storage import MessageStore
from forge.llm.token_counter import TokenCounter
from forge.memory.base import (
    Fact,
    FactRecallRequest,
    MemoryStore,
    MemoryStoreError,
    Summary,
)
from forge.observability.tracing.tracer import span

logger = logging.getLogger(__name__)


class CompositeContextBuilder:
    """无状态. 同一实例可并发处理多个请求.

    Note:
        MessageStore (S6.5 M3) 的本地实现仍是 MessageRepository, 持有一个 DB session,
        因此该 builder 应在 chat 路由的 SSE 生成器里 "按需 new", 用完即弃
        (与 _stream_chat 的 session 生命周期对齐).
        memory_store / token_counter 是长寿对象, 应用启动时注入.
    """

    def __init__(
        self,
        history_repo: MessageStore,
        memory_store: MemoryStore,
        token_counter: TokenCounter,
    ):
        self._history_repo = history_repo
        self._memory = memory_store
        self._counter = token_counter

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    async def build(self, request: BuildRequest) -> AssembledContext:
        request.budget.validate()
        meta = BuildMeta()

        with span(
            "context.build",
            session_id=request.session_id,
            user_id=request.user_id,
            history_limit=request.agent.history_limit,
            context_window=request.agent.context_window,
            workspace_id=request.workspace_id,
            workflow_id=request.workflow_id,
        ) as s:
            # 1. 并发拉三路素材 (memory 失败被 _safe_* 吞掉)
            history_raw, summary, facts = await asyncio.gather(
                self._history_repo.load_recent(
                    request.session_id, limit=request.agent.history_limit
                ),
                self._safe_get_summary(request, meta),
                self._safe_recall_facts(request, meta),
            )

            # 2. 拼 system message
            system_text = self._compose_system_message(
                base_prompt=request.agent.system_prompt,
                workspace=request.workspace_context,
                project_decisions=request.project_decisions,
                workflow=request.workflow_context,
                role_history=request.role_history,
                facts=facts,
                summary=summary,
            )
            system_msg = Message(role="system", content=system_text)
            if summary is not None:
                meta.summary_included = True
            meta.facts_included = len(facts)

            # 3. 历史: ORM -> Message, 按 token 预算从新到旧累加
            history_msgs = self._orm_to_messages(
                history_raw, exclude_ids=set(request.exclude_message_ids)
            )
            history_budget = int(request.agent.context_window * request.budget.history_share)
            kept_history, dropped = self._trim_history_by_budget(history_msgs, history_budget)
            meta.history_messages_used = len(kept_history)
            meta.history_messages_dropped = dropped
            if dropped > 0:
                meta.degraded.append("history_truncated_by_budget")

            # 4. 拼最终 messages.
            # 当前用户消息包 <current_question> 标签, 配合 system prompt 里的
            # "只回答 <current_question> 内的内容" 指令, 显著降低串轮概率.
            current_msg = Message(
                role="user",
                content=(
                    f"<current_question>\n{request.current_user_message}\n</current_question>"
                ),
            )
            messages = [system_msg, *kept_history, current_msg]
            meta.estimated_input_tokens = self._counter.count_messages(messages)

            # 5. 把 meta 全部铺到 span 属性 (LoggingTracer 一条 INFO 即可,
            #    OTel/Langfuse 会变成结构化 attributes)
            if request.workspace_id:
                s.set("workspace_id", request.workspace_id)
            if request.workflow_id:
                s.set("workflow_id", request.workflow_id)
            s.set("estimated_input_tokens", meta.estimated_input_tokens)
            s.set("history_messages_used", meta.history_messages_used)
            s.set("history_messages_dropped", meta.history_messages_dropped)
            s.set("summary_included", meta.summary_included)
            s.set("facts_included", meta.facts_included)
            s.set("degraded", list(meta.degraded))

            return AssembledContext(messages=messages, meta=meta)

    # ------------------------------------------------------------------
    # MemoryStore 安全包装: 失败 -> 降级, 不抛
    # ------------------------------------------------------------------
    async def _safe_get_summary(self, request: BuildRequest, meta: BuildMeta) -> Summary | None:
        if not request.agent.enable_summary:
            return None
        try:
            return await self._memory.get_summary(
                request.session_id,
                workspace_id=request.workspace_id,
            )
        except MemoryStoreError as exc:
            logger.warning("取摘要失败: %s", exc)
            meta.degraded.append("summary_fetch_failed")
            return None

    async def _safe_recall_facts(self, request: BuildRequest, meta: BuildMeta) -> list[Fact]:
        if not request.agent.enable_facts:
            return []
        try:
            return await self._memory.recall_facts(
                FactRecallRequest(
                    user_id=request.user_id,
                    query=request.current_user_message,
                    top_k=request.agent.facts_top_k,
                )
            )
        except MemoryStoreError as exc:
            logger.warning("召回长期事实失败: %s", exc)
            meta.degraded.append("facts_recall_failed")
            return []

    # ------------------------------------------------------------------
    # 系统消息组装 (内联 composer, 后续若复杂可拆 composer.py)
    # ------------------------------------------------------------------
    @staticmethod
    def _compose_system_message(
        *,
        base_prompt: str,
        workspace: WorkspaceContextLayer | None = None,
        project_decisions: tuple[str, ...] = (),
        workflow: WorkflowContextLayer | None = None,
        role_history: tuple[str, ...] = (),
        facts: list[Fact],
        summary: Summary | None,
    ) -> str:
        parts: list[str] = [base_prompt.strip()] if base_prompt else []

        if workspace is not None:
            workspace_parts = [
                "## Workspace Context",
                f"- workspace_id: {workspace.workspace_id}",
                f"- root_path: {workspace.root_path}",
            ]
            if workspace.assistant_prompt.strip():
                workspace_parts.append("### ASSISTANT.md\n" + workspace.assistant_prompt.strip())
            if workspace.settings:
                import json

                workspace_parts.append(
                    "### Settings\n"
                    "```json\n"
                    f"{json.dumps(workspace.settings, ensure_ascii=False, sort_keys=True)}\n"
                    "```"
                )
            parts.append("\n".join(workspace_parts))

        if project_decisions:
            lines = ["## Project Decisions"]
            lines.extend(f"- {item}" for item in project_decisions if item.strip())
            if len(lines) > 1:
                parts.append("\n".join(lines))

        if workflow is not None:
            lines = [
                "## Workflow Context",
                f"- workflow_id: {workflow.workflow_id}",
                f"- template_id: {workflow.template_id}",
                f"- mode: {workflow.mode}",
            ]
            if workflow.role_artifacts:
                lines.append("### Role Artifacts")
                for role, items in workflow.role_artifacts.items():
                    lines.append(f"- {role}: {items}")
            if workflow.recent_events:
                lines.append("### Recent Events")
                for event in workflow.recent_events[-5:]:
                    event_type = event.get("type", "-")
                    payload = event.get("payload", {})
                    lines.append(f"- {event_type}: {payload}")
            parts.append("\n".join(lines))

        if role_history:
            lines = ["## Role History"]
            lines.extend(f"- {item}" for item in role_history if item.strip())
            if len(lines) > 1:
                parts.append("\n".join(lines))

        if facts:
            lines = ["## 关于用户 (长期记忆)"]
            lines.extend(f"- {f.content}" for f in facts)
            parts.append("\n".join(lines))

        if summary is not None and summary.content:
            parts.append("## 早期对话摘要\n" + summary.content.strip())

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # ORM -> Message 转换
    # ------------------------------------------------------------------
    @staticmethod
    def _orm_to_messages(rows, *, exclude_ids: set[str]) -> list[Message]:
        """过滤掉空内容 / 非 user/assistant 的行 (system 由 builder 自己造).

        exclude_ids 通常含本轮已持久化但属于 "当前用户输入" 的消息 id.
        """
        out: list[Message] = []
        for r in rows:
            if r.id in exclude_ids:
                continue
            if r.role not in ("user", "assistant") or not r.content:
                continue
            out.append(Message(role=r.role, content=r.content))
        return out

    # ------------------------------------------------------------------
    # token 预算裁剪: 从最新往前累加, 早期超额者丢弃
    # ------------------------------------------------------------------
    def _trim_history_by_budget(
        self, messages: list[Message], budget: int
    ) -> tuple[list[Message], int]:
        if budget <= 0 or not messages:
            return [], len(messages)

        kept_reversed: list[Message] = []
        used = 0
        for m in reversed(messages):
            cost = self._counter.count_messages([m])
            if used + cost > budget:
                break
            kept_reversed.append(m)
            used += cost

        kept = list(reversed(kept_reversed))
        dropped = len(messages) - len(kept)
        return kept, dropped
