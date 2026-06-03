"""MessageAssembler: 把 rendered_prompt + ContentChunks 组装成最终 messages.

组装顺序 (等价 CompositeContextBuilder._compose_system_message):

    system message =
        rendered_prompt              # system_prompt 层
        + workspace chunks           # workspace 层 (workspace/project/workflow/role)
        + facts chunk                # facts 层
        + summary chunk              # summary 层

    final messages = [system, *kept_history, current_user_in_question_tag]

历史裁剪策略 (与原实现等价):
    从最新往前累加, 超 dialogue_budget 时停止, 早期消息丢弃.

输出: ContextSnapshot, 含完整 ContextUsage (近实时分层用量).
"""

from __future__ import annotations

import logging

from forge.context_mgmt.builder.content_gatherer import GatherResult
from forge.context_mgmt.protocols import TokenMeter
from forge.context_mgmt.types import (
    ContentChunk,
    ContextRequest,
    ContextSnapshot,
    ContextUsage,
    LayerUsage,
    WindowBudget,
)
from forge.core.types.message import Message

logger = logging.getLogger(__name__)


class MessageAssembler:
    """无状态. 同一实例可并发处理多个请求."""

    def __init__(self, token_meter: TokenMeter) -> None:
        self._meter = token_meter

    def assemble(
        self,
        request: ContextRequest,
        rendered_prompt: str,
        gather_result: GatherResult,
        budget: WindowBudget,
    ) -> ContextSnapshot:
        chunks = gather_result.chunks

        # ---- 1. 组装 system message 文本 ----
        system_text, workspace_tokens = self._build_system_text(
            rendered_prompt, chunks
        )
        system_msg = Message(role="system", content=system_text)

        # ---- 2. 历史裁剪 (按 dialogue_budget) ----
        history_chunk = self._first_chunk(chunks, "history")
        history_messages = history_chunk.messages if history_chunk else []
        # Provider 已算好的每条 token 数 (digest 折叠后), 裁剪时直接用免重复 tiktoken
        history_costs = history_chunk.message_tokens if history_chunk else []
        candidate = history_chunk.message_count if history_chunk else 0
        candidate_was_filtered = history_chunk.truncated if history_chunk else False

        kept_history, dropped, dlg_tokens = self._trim_history_by_budget(
            history_messages, history_costs, budget.dialogue_budget
        )
        history_filtered_count = (
            (candidate - len(history_messages)) if candidate_was_filtered else 0
        )
        # 降级职责收敛: 单条超长已由 DigestPolicy 折叠保留 (anchor+引用, 可回读);
        # 这里的「硬丢弃」是 digest 折叠后整体仍超 dialogue_budget 时的最后兜底,
        # 与折叠是两种不同的「降级」—— 折叠保留信息可回读, 硬丢弃则整条移出窗口。
        if dropped > 0:
            history_chunk_flags = (
                (history_chunk.degraded + history_chunk.info) if history_chunk else []
            )
            logger.info(
                "history 累计预算裁剪: 保留=%d 硬丢弃=%d dialogue_budget=%d 折叠标记=%s",
                len(kept_history), dropped, budget.dialogue_budget,
                history_chunk_flags or "无",
            )

        # ---- 3. 当前用户消息 (包 <current_question> 标签) ----
        current_msg = Message(
            role="user",
            content=(
                f"<current_question>\n"
                f"{request.current_user_message}\n"
                f"</current_question>"
            ),
        )

        # ---- 4. 拼最终 messages ----
        messages = [system_msg, *kept_history, current_msg]
        total_tokens = self._meter.count_messages(messages)

        # ---- 5. 聚合各层用量 ----
        layers = self._aggregate_layers(
            rendered_prompt=rendered_prompt,
            chunks=chunks,
            workspace_tokens=workspace_tokens,
            kept_history=kept_history,
            dlg_tokens=dlg_tokens,
            current_msg=current_msg,
            budget=budget,
        )

        usage = ContextUsage(
            context_window=budget.context_window,
            total_input_tokens=total_tokens,
            max_output_tokens=max(0, budget.context_window - total_tokens),
            total_ratio=total_tokens / budget.context_window if budget.context_window else 0.0,
            layers=layers,
        )

        # ---- 6. 组装 snapshot ----
        summary_included = bool(self._first_chunk(chunks, "summary"))
        facts_chunk = self._first_chunk(chunks, "facts")
        facts_included = facts_chunk.message_count if facts_chunk else 0

        snapshot = ContextSnapshot(
            messages=messages,
            budget=budget,
            usage=usage,
            rendered_system_prompt=rendered_prompt,
            history_messages_candidate=candidate,
            history_messages_used=len(kept_history),
            history_messages_filtered=history_filtered_count,
            history_messages_dropped=dropped,
            summary_included=summary_included,
            facts_included=facts_included,
        )

        # 降级原因汇合
        if dropped > 0:
            snapshot.degraded.append("history_truncated_by_budget")
        snapshot.degraded.extend(gather_result.degraded)
        # 合并各 chunk 自身产生的降级 / 信息标记 (如 HistoryProvider 的
        # digest_pending / digest_substituted), 去重
        for chunk_list in chunks.values():
            for c in chunk_list:
                for flag in c.degraded:
                    if flag not in snapshot.degraded:
                        snapshot.degraded.append(flag)
                for flag in c.info:
                    if flag not in snapshot.info:
                        snapshot.info.append(flag)

        return snapshot

    # ------------------------------------------------------------------
    # 内部: 组装 system text + 统计 workspace 层 token
    # ------------------------------------------------------------------
    def _build_system_text(
        self,
        rendered_prompt: str,
        chunks: dict[str, list[ContentChunk]],
    ) -> tuple[str, int]:
        """按 base → workspace → facts → summary 顺序拼 system 文本.

        Returns:
            (system_text, workspace_tokens)
        """
        parts: list[str] = []
        workspace_tokens = 0

        if rendered_prompt and rendered_prompt.strip():
            parts.append(rendered_prompt.strip())

        # workspace 层 (含 workspace/project_decisions/workflow/role_history)
        for c in chunks.get("workspace", []):
            if c.text:
                parts.append(c.text)
                workspace_tokens += c.estimated_tokens

        # facts 层
        for c in chunks.get("facts", []):
            if c.text:
                parts.append(c.text)

        # summary 层
        for c in chunks.get("summary", []):
            if c.text:
                parts.append(c.text)

        return "\n\n".join(parts), workspace_tokens

    # ------------------------------------------------------------------
    # 内部: 历史裁剪 (从最新往前累加, 超预算停止)
    # ------------------------------------------------------------------
    def _trim_history_by_budget(
        self, messages: list[Message], costs: list[int], budget: int
    ) -> tuple[list[Message], int, int]:
        """按 dialogue_budget 从最新往旧累加裁剪.

        costs: 与 messages 等长的每条 token 数 (Provider 已算好, 通常是 digest 折叠后体积);
               长度不匹配 / 为空时回退实时 count (兼容)。
        返回 (kept, dropped, kept_tokens)。kept_tokens 供 dialogue 层用量直接复用。
        """
        if budget <= 0 or not messages:
            return [], len(messages), 0
        use_costs = costs if (costs and len(costs) == len(messages)) else None
        kept_reversed: list[Message] = []
        used = 0
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            cost = use_costs[i] if use_costs is not None else self._meter.count_messages([m])
            if used + cost > budget:
                break
            kept_reversed.append(m)
            used += cost
        kept = list(reversed(kept_reversed))
        return kept, len(messages) - len(kept), used

    # ------------------------------------------------------------------
    # 内部: 聚合各层 token 用量, 产出 LayerUsage 列表
    # ------------------------------------------------------------------
    def _aggregate_layers(
        self,
        *,
        rendered_prompt: str,
        chunks: dict[str, list[ContentChunk]],
        workspace_tokens: int,
        kept_history: list[Message],
        dlg_tokens: int,
        current_msg: Message,
        budget: WindowBudget,
    ) -> list[LayerUsage]:
        cw = budget.context_window
        layers: list[LayerUsage] = []

        # 1. system_prompt 层
        sys_tokens = self._meter.count_text(rendered_prompt) if rendered_prompt else 0
        layers.append(LayerUsage(
            name="system_prompt",
            token_count=sys_tokens,
            ratio=sys_tokens / cw if cw else 0.0,
        ))

        # 2. workspace 层 (合并多个 chunk) — chat 模式无 workspace_context 时不记录
        ws_chunks = chunks.get("workspace", [])
        if workspace_tokens > 0:
            layers.append(LayerUsage(
                name="workspace",
                token_count=workspace_tokens,
                ratio=workspace_tokens / cw if cw else 0.0,
                message_count=len(ws_chunks),
            ))

        # 3. facts 层
        facts_chunk = self._first_chunk(chunks, "facts")
        facts_tokens = facts_chunk.estimated_tokens if facts_chunk else 0
        facts_count = facts_chunk.message_count if facts_chunk else 0
        layers.append(LayerUsage(
            name="facts",
            token_count=facts_tokens,
            ratio=facts_tokens / cw if cw else 0.0,
            message_count=facts_count,
        ))

        # 4. summary 层
        summary_chunk = self._first_chunk(chunks, "summary")
        summary_tokens = summary_chunk.estimated_tokens if summary_chunk else 0
        layers.append(LayerUsage(
            name="summary",
            token_count=summary_tokens,
            ratio=summary_tokens / cw if cw else 0.0,
        ))

        # 5. dialogue 层 (用 trim 累加出的 kept_tokens, 免再次 count_messages)
        history_chunk = self._first_chunk(chunks, "history")
        dlg_truncated = (
            history_chunk is not None
            and history_chunk.message_count > len(kept_history)
        )
        layers.append(LayerUsage(
            name="dialogue",
            token_count=dlg_tokens,
            ratio=dlg_tokens / cw if cw else 0.0,
            message_count=len(kept_history),
            truncated=dlg_truncated,
        ))

        # 6. tool_results 层 (阶段 1 暂为 0, 阶段 2 在 HistoryProvider 拆分后填充)
        layers.append(LayerUsage(
            name="tool_results",
            token_count=0,
            ratio=0.0,
            message_count=0,
        ))

        # 7. current_input 层
        cur_tokens = self._meter.count_messages([current_msg])
        layers.append(LayerUsage(
            name="current_input",
            token_count=cur_tokens,
            ratio=cur_tokens / cw if cw else 0.0,
            message_count=1,
        ))

        return layers

    # ------------------------------------------------------------------
    # 内部小工具
    # ------------------------------------------------------------------
    @staticmethod
    def _first_chunk(
        chunks: dict[str, list[ContentChunk]], name: str
    ) -> ContentChunk | None:
        items = chunks.get(name, [])
        return items[0] if items else None
