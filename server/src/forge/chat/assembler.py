"""ContextAssembler: 上下文组装的剥离层.

职责:
    1. 渲染 system_prompt (chat 模板, 注入 agent.system_prompt 用户段)
    2. 调 ContextBuilder.build() 拼最终 messages
    3. 主动压缩 (R2): 算出 token 超 window * 阈值 或 history 已被丢弃时,
       inline 触发 SummaryService 重生成摘要, 然后 rebuild context.

主动压缩流程由 Orchestrator 驱动 (它要在前后 yield compaction_started /
compaction_done 事件), ContextAssembler 暴露三个原子操作:
    - assemble(ctx, snapshot)                    -> 初次组装
    - should_compact(result, ctx)                -> 是否需要压缩 (检查阈值 / 降级)
    - compact_and_reassemble(ctx, snapshot, prev) -> 跑 SummaryService + rebuild

不做的事:
    - LLM 选型      -> chat.llm_selection
    - DB 会话       -> chat.preparer
    - 发 SSE 事件   -> chat.orchestrator
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from config.settings import get_settings

from forge.chat.preparer import _AgentSnapshot
from forge.chat.types import TurnContext
from forge.context.base import (
    AgentContextConfig,
    AssembledContext,
    BuildRequest,
    WorkspaceContextLayer,
)
from forge.context.factory import build_context_builder
from forge.infrastructure.database.database import get_session_factory

# Storage protocol injected, swap by deployment_mode (S6.5 M3).
from forge.infrastructure.storage import (
    make_knowledge_base_store,
    make_message_store,
)
from forge.observability.tracing.tracer import span
from forge.prompts import get_registry
from forge.workspace import load_workspace_context

logger = logging.getLogger(__name__)


class ContextAssembler:
    """无状态. 每次 assemble() 自有 DB session (跟 chat 的请求 session 解耦)."""

    SYSTEM_TEMPLATE = "chat/default_system"
    # estimated_input_tokens / context_window 超此比例触发主动压缩
    DEFAULT_COMPACTION_THRESHOLD: float = 0.85

    def __init__(self, compaction_threshold: float | None = None) -> None:
        self._threshold = compaction_threshold or self.DEFAULT_COMPACTION_THRESHOLD

    # ------------------------------------------------------------------
    # 主入口: 初次组装
    # ------------------------------------------------------------------
    async def assemble(
        self, ctx: TurnContext, agent_snapshot: _AgentSnapshot
    ) -> tuple[AssembledContext, str]:
        with span(
            "chat.assemble",
            session_id=ctx.session_id,
            context_window=ctx.context_window,
            workspace_id=ctx.workspace_id,
            workflow_id=ctx.workflow_id,
        ) as s:
            system_prompt = await self._render_system_prompt(ctx, agent_snapshot)
            result = await self._build_once(ctx, system_prompt)
            s.set("estimated_input_tokens", result.meta.estimated_input_tokens)
            s.set("history_used", result.meta.history_messages_used)
            s.set("history_dropped", result.meta.history_messages_dropped)
            s.set("summary_included", result.meta.summary_included)
            s.set("facts_included", result.meta.facts_included)
            if result.meta.degraded:
                s.set("degraded", list(result.meta.degraded))
        return result, system_prompt

    # ------------------------------------------------------------------
    # 压缩判定: 何时该压缩
    # ------------------------------------------------------------------
    def should_compact(self, result: AssembledContext, ctx: TurnContext) -> bool:
        """返回 True 表示需要主动压缩.

        触发条件 (任一满足):
            1. estimated_input_tokens 占 context_window 超过阈值 (默认 85%)
            2. history_messages_dropped > 0 (已经有信息因预算被丢弃, 必须补救)

        记忆系统关闭时, 永远 False -- 没有 SummaryStore 没法压缩.
        """
        try:
            if not get_settings().memory.enabled:
                return False
        except Exception:  # noqa: BLE001
            return False

        if result.meta.history_messages_dropped > 0:
            return True

        if ctx.context_window > 0:
            ratio = result.meta.estimated_input_tokens / ctx.context_window
            if ratio > self._threshold:
                return True
        return False

    # ------------------------------------------------------------------
    # 压缩 + 重建
    # ------------------------------------------------------------------
    async def compact_and_reassemble(
        self,
        ctx: TurnContext,
        agent_snapshot: _AgentSnapshot,
        prev: AssembledContext,
    ) -> tuple[AssembledContext, str, int]:
        """跑一次 inline 摘要 + 重新拼接上下文.

        Returns:
            (new_result, system_prompt, tokens_saved):
                tokens_saved = max(0, prev_tokens - new_tokens). 失败时 = 0.

        失败处理:
            SummaryService 抛 InfrastructureError -> 用 prev 结果继续, 在 meta.degraded
            里追加 "compaction_failed". 用户对话不受影响.
        """
        from forge.memory.summary.service import (
            InfrastructureError,
            get_summary_service,
        )

        prev_tokens = prev.meta.estimated_input_tokens
        with span(
            "chat.compact",
            session_id=ctx.session_id,
            pre_tokens=prev_tokens,
            context_window=ctx.context_window,
            history_dropped=prev.meta.history_messages_dropped,
        ) as s:
            try:
                await get_summary_service().summarize_session(
                    ctx.session_id,
                    workspace_id=ctx.workspace_id,
                )
            except InfrastructureError as exc:
                logger.warning(
                    "inline 压缩失败 session=%s: %s -- 回退到压缩前的上下文",
                    ctx.session_id,
                    exc,
                )
                prev.meta.degraded.append("compaction_failed")
                s.set("ok", False)
                s.set("failure", "summarize_service_error")
                s.set_error(exc)
                return prev, "", 0
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "inline 压缩崩溃 session=%s -- 回退到压缩前的上下文",
                    ctx.session_id,
                )
                prev.meta.degraded.append("compaction_failed")
                s.set("ok", False)
                s.set("failure", "unexpected_error")
                s.set_error(exc)
                return prev, "", 0

            system_prompt = await self._render_system_prompt(ctx, agent_snapshot)
            new_result = await self._build_once(ctx, system_prompt)

            new_result.meta.compaction_performed = True
            new_result.meta.rebuild_count = (prev.meta.rebuild_count or 0) + 1
            tokens_saved = max(0, prev_tokens - new_result.meta.estimated_input_tokens)
            new_result.meta.compaction_token_saved = tokens_saved
            s.set("ok", True)
            s.set("post_tokens", new_result.meta.estimated_input_tokens)
            s.set("tokens_saved", tokens_saved)
            s.set("rebuild_count", new_result.meta.rebuild_count)
        new_result.meta.degraded.append("active_compaction_triggered")

        logger.info(
            "主动压缩完成 session=%s tokens %d -> %d (节省 %d)",
            ctx.session_id,
            prev_tokens,
            new_result.meta.estimated_input_tokens,
            tokens_saved,
        )
        return new_result, system_prompt, tokens_saved

    # ------------------------------------------------------------------
    # 内部: 调 ContextBuilder.build 一次
    # ------------------------------------------------------------------
    async def _build_once(self, ctx: TurnContext, system_prompt: str) -> AssembledContext:
        factory = get_session_factory()
        async with factory() as db:
            msg_repo = make_message_store(db)
            builder = build_context_builder(msg_repo)
            workspace_layer = ctx.workspace_context or _load_workspace_layer(ctx.workspace_id)
            result = await builder.build(
                BuildRequest(
                    user_id=ctx.user_id,
                    session_id=ctx.session_id,
                    current_user_message=ctx.current_user_message,
                    agent=AgentContextConfig(
                        system_prompt=system_prompt,
                        context_window=ctx.context_window,
                    ),
                    exclude_message_ids=ctx.exclude_message_ids,
                    workspace_id=ctx.workspace_id,
                    workflow_id=ctx.workflow_id,
                    workspace_context=workspace_layer,
                    project_decisions=ctx.project_decisions,
                    workflow_context=ctx.workflow_context,
                    role_history=ctx.role_history,
                )
            )
        logger.info(
            "上下文构建完成 session=%s messages=%d est_input_tokens=%d "
            "history_used=%d history_dropped=%d facts=%d summary=%s degraded=%s",
            ctx.session_id,
            len(result.messages),
            result.meta.estimated_input_tokens,
            result.meta.history_messages_used,
            result.meta.history_messages_dropped,
            result.meta.facts_included,
            result.meta.summary_included,
            result.meta.degraded or "[]",
        )
        if result.meta.degraded:
            logger.warning(
                "上下文构建有降级 session=%s reasons=%s",
                ctx.session_id,
                result.meta.degraded,
            )
        return result

    # ------------------------------------------------------------------
    # 内部: system prompt 渲染
    # ------------------------------------------------------------------
    async def _render_system_prompt(self, ctx: TurnContext, agent: _AgentSnapshot) -> str:
        """渲染 chat 系统提示词.

        用户自定义段 (agent.system_prompt) 作为纯文本变量注入模板槽位,
        框架固定段 (响应规则 / 串轮防御 / 输出格式) 由模板自带, 用户不可覆盖.
        可用 KB 列表在此处一次性查出注入 prompt, 避免 LLM 再开一个 tool round-trip
        去问 "我能访问哪些知识库".
        """
        # 工具元数据从全局 ToolRegistry 取, 让模板可以列出 LLM 可用工具.
        # 后续支持 per-agent 工具白名单时, 改成按 agent.tool_names 过滤.
        from forge.tools.registry import ToolRegistry

        tools_meta = [
            {"name": t.name, "description": t.description} for t in ToolRegistry.get_all()
        ]
        kb_list = await self._fetch_kb_list(ctx.user_id)
        workspace_ctx = load_workspace_context()
        user_prompt = _merge_user_prompt(
            agent.system_prompt or "",
            workspace_ctx.assistant_prompt,
            workspace_ctx.settings,
        )
        return get_registry().render(
            self.SYSTEM_TEMPLATE,
            user_system_prompt=user_prompt,
            # agent_name=agent.name,
            user_name=ctx.user_name,
            datetime=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tools=tools_meta,
            kb_list=kb_list,
        )

    async def _fetch_kb_list(self, user_id: str) -> list[dict]:
        """查当前用户可访问的 KB. 失败时返回空列表 (降级, 不阻塞对话).

        返回 None 表示"功能不可用"(用模板里 `kb_list is defined` 区分);
        返回 [] 表示"用户没 KB"(模板会显式告知 LLM 不要调 knowledge_search).
        当前实现 DB 失败时返回 [] 等同于"无 KB", 让 LLM 走通用回答路径.
        """
        if not user_id:
            return []
        try:
            factory = get_session_factory()
            async with factory() as db:
                kb_repo = make_knowledge_base_store(db)
                kbs = await kb_repo.list_for_user(user_id)
                return [
                    {
                        "name": kb.name,
                        "description": (kb.description or "").strip(),
                        "document_count": kb.document_count or 0,
                    }
                    for kb in kbs
                ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("拉取可用 KB 列表失败 user=%s: %s", user_id, exc)
            return []


def _merge_user_prompt(
    agent_prompt: str,
    workspace_prompt: str,
    workspace_settings: dict | None = None,
) -> str:
    agent_prompt = (agent_prompt or "").strip()
    workspace_prompt = (workspace_prompt or "").strip()
    sections: list[str] = []
    if agent_prompt:
        sections.append(agent_prompt)
    if workspace_prompt:
        sections.append(f"## Workspace Rules (from .assistant/ASSISTANT.md)\n{workspace_prompt}")
    settings = workspace_settings or {}
    if settings:
        sections.append(
            "## Workspace Settings (from .assistant/settings*.json)\n"
            "```json\n"
            f"{json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True)}\n"
            "```"
        )
    return "\n\n".join(sections).strip()


def _load_workspace_layer(workspace_id: str | None) -> WorkspaceContextLayer:
    start = None
    if workspace_id:
        from pathlib import Path

        start = Path(workspace_id)
    ctx = load_workspace_context(start=start)
    return WorkspaceContextLayer(
        workspace_id=workspace_id or str(ctx.root_path),
        root_path=str(ctx.root_path),
        assistant_prompt=ctx.assistant_prompt,
        settings=ctx.settings,
    )
