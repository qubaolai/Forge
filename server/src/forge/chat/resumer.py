"""TurnResumer: 把 aborted / partial 的 assistant 消息继续生成.

跟 TurnPreparer 对称:
    - Preparer:  新建 user_msg + 占位 assistant_msg
    - Resumer:   加载现有 assistant_msg, 校验, 状态回滚到 streaming

不创建新消息, 不创建新 session. Finalizer 在结束时 merge 新内容到 prev_content.

合法的入口状态:
    aborted     - 用户 /chat/stop
    partial     - 系统 LoopGuard 强制收尾 (R5 加进 DB 状态)

拒绝的状态:
    done        - 已完成, 不该继续
    error       - 不可恢复
    streaming   - 别处正在跑 (并发 resume 防护)

异常:
    ResumeError("...", code) -> Orchestrator 转 SSE error 事件
"""

from __future__ import annotations

import logging

from forge.chat.preparer import _AgentSnapshot
from forge.chat.types import ResumeState, TurnContext
from forge.infrastructure.database.database import get_session_factory
from forge.infrastructure.database.repositories.agent_repo import (
    AgentRepository,
)

# Storage protocol injected, swap by deployment_mode (S6.5 M3).
from forge.infrastructure.storage import make_message_store, make_session_store
from forge.observability.tracing.tracer import span
from forge.prompts import get_registry

logger = logging.getLogger(__name__)

# Resume 提示词模板 (prompts/chat/resume.j2). 由 TurnResumer 渲染, 喂给
# ContextBuilder 作为 current_user_message, 会被自动包 <current_question> 标签.
RESUME_PROMPT_TEMPLATE = "chat/resume"

# 从 prev_content 末尾抓多少字符做 "上次写到哪儿" 的锚点.
# 80 足够给 LLM 定位但不至于在 prompt 里占太多 token.
SUFFIX_ANCHOR_CHARS = 80


def _suffix_anchor(text: str, max_chars: int = SUFFIX_ANCHOR_CHARS) -> str:
    """取 text 末尾 max_chars 个字符做 LLM 续写锚点."""
    if not text:
        return ""
    return text[-max_chars:]


def _inside_unclosed_fence(text: str) -> bool:
    """text 末尾是否处在一个未闭合的 ``` 代码块内.

    简单计数: 奇数个 ``` 标记 -> 当前在代码块内.
    边界情况 (代码内出现 ``` 字符串) 罕见, 误判后果可控 (只是多给 LLM 一个 hint).
    """
    if not text:
        return False
    return text.count("```") % 2 == 1


def _inside_unclosed_table_row(text: str) -> bool:
    """text 最后一行是否是未闭合的 markdown 表格行 (`|` 开头但未以 `|` 结尾)."""
    if not text:
        return False
    last_line = text.rsplit("\n", 1)[-1].rstrip()
    if not last_line.startswith("|"):
        return False
    # 单独 "|" 也视为不完整
    return not last_line.endswith("|") or last_line == "|"


def _render_resume_prompt(prev_content: str) -> str:
    """根据 prev_content 抽取结构特征, 渲染 resume 提示词."""
    anchor = _suffix_anchor(prev_content)
    return get_registry().render(
        RESUME_PROMPT_TEMPLATE,
        suffix_anchor=anchor,
        anchor_chars=len(anchor),
        in_code_block=_inside_unclosed_fence(prev_content),
        in_table=_inside_unclosed_table_row(prev_content),
    )


_RESUMABLE_STATUSES = {"aborted", "partial"}


class ResumeError(Exception):
    """resume 阶段不可恢复错误. Orchestrator 转 SSE error."""

    def __init__(self, message: str, code: str = "40000") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class TurnResumer:
    """无状态. 每次 prepare() 自有 DB 事务."""

    async def prepare(
        self,
        *,
        user_id: str,
        message_id: str,
        trace_id: str,
    ) -> tuple[TurnContext, _AgentSnapshot, ResumeState]:
        """加载 assistant_msg, 校验, 重置 streaming, 返回所需上下文.

        步骤:
            1. 取 assistant_msg + 校验存在
            2. 取 session + 校验所有权
            3. 校验 status ∈ {aborted, partial}
            4. 取 parent (原 user_msg) -> 拿原始问题文本
            5. 取 agent (若有)
            6. 把 assistant_msg.status 置回 "streaming" + 同事务 commit
            7. 构造 ResumeState (含 prev_*) + TurnContext + _AgentSnapshot
        """
        factory = get_session_factory()
        with span("chat.resume.prepare", user_id=user_id, message_id=message_id) as s:
            async with factory() as db:
                msg_repo = make_message_store(db)
                sess_repo = make_session_store(db)
                agent_repo = AgentRepository(db)  # AgentStore Protocol 未列入 S6.5

                asst = await msg_repo.get_by_id(message_id)
                if asst is None or asst.role != "assistant":
                    raise ResumeError("assistant 消息不存在", code="40440")

                session = await sess_repo.get_by_id(asst.session_id)
                if session is None or session.user_id != user_id:
                    raise ResumeError("无权访问该会话", code="40310")

                if asst.status not in _RESUMABLE_STATUSES:
                    raise ResumeError(
                        f"当前状态 {asst.status!r} 不可恢复 (仅支持 aborted / partial)",
                        code="40901",
                    )

                if asst.parent_id is None:
                    raise ResumeError("找不到原始用户消息 (parent_id 缺失)", code="40441")
                parent = await msg_repo.get_by_id(asst.parent_id)
                if parent is None or parent.role != "user":
                    raise ResumeError("原始用户消息已删除", code="40441")

                # agent + mode (跟 Preparer 同款)
                agent_orm = None
                if session.agent_id and session.agent_id != "default":
                    agent_orm = await agent_repo.get_by_id(session.agent_id)
                mode = getattr(agent_orm, "mode", None) or "react"

                # 先在状态被改之前抓快照 (update 之后 asst.status 会变成 "streaming")
                session_id = session.id
                assistant_msg_id = asst.id
                user_msg_id = parent.id
                user_msg_content = parent.content or ""
                prev_content = asst.content or ""
                prev_tool_calls = list(asst.tool_calls or [])
                prev_reasoning = asst.reasoning_content
                prev_reasoning_ms = asst.reasoning_duration_ms
                prev_usage = dict(asst.usage or {})
                prev_status = asst.status
                prev_finish_reason = (asst.context_meta or {}).get("finish_reason", prev_status)

                # ★ 关键: 状态置回 streaming, 让本轮可以再注册 abort_event,
                # 并防止并发 resume 互相覆盖.
                await msg_repo.update(asst, status="streaming")
                await db.commit()

                s.set("session_id", session_id)
                s.set("prev_status", prev_status)
                s.set("prev_content_len", len(prev_content))
                s.set("prev_tool_calls", len(prev_tool_calls))
                s.set("agent_mode", mode)

        snapshot = _AgentSnapshot(
            agent_id=session.agent_id if session.agent_id != "default" else None,
            mode=mode,
            name=getattr(agent_orm, "name", None),
            system_prompt=(getattr(agent_orm, "system_prompt", "") or ""),
            model_id=getattr(agent_orm, "model_id", None),
            context_window=getattr(agent_orm, "context_window", 128_000),
        )

        # 渲染续写提示词: 含 suffix_anchor (上次末尾 80 字符) + 结构感知 (代码块/表格)
        resume_prompt = _render_resume_prompt(prev_content)

        ctx = TurnContext(
            user_id=user_id,
            user_name="",  # resume 时不再用 user_name (system 已渲染过), 留空
            session_id=session_id,
            assistant_msg_id=assistant_msg_id,
            user_msg_id=user_msg_id,
            current_user_message=resume_prompt,
            agent_id=snapshot.agent_id,
            agent_mode=snapshot.mode,
            is_new_session=False,
            new_title=None,
            trace_id=trace_id,
            model_options=None,
            # 不排除任何 message: 原 user_msg + 上次 streaming 时其他持久化的消息
            # 都要进 history. partial assistant 因 status=streaming (刚才置的),
            # load_recent 只返回 status="done" 不会包含它, 后续 Orchestrator
            # 手工把 partial assistant 拼进 messages 末端.
            exclude_message_ids=(),
            context_window=snapshot.context_window,
        )

        resume = ResumeState(
            original_user_message=user_msg_content,
            prev_content=prev_content,
            prev_tool_calls=prev_tool_calls,
            prev_reasoning_content=prev_reasoning,
            prev_reasoning_duration_ms=prev_reasoning_ms,
            prev_usage=prev_usage,
            prev_finish_reason=prev_finish_reason,
            prev_status=prev_status,
        )

        logger.info(
            "续写准备完成 session=%s message_id=%s prev_status=%s prev_content_len=%d "
            "prev_tool_calls=%d",
            session_id,
            assistant_msg_id,
            prev_status,
            len(prev_content),
            len(prev_tool_calls),
            extra={
                "session_id": session_id,
                "user_id": user_id,
                "message_id": assistant_msg_id,
            },
        )
        return ctx, snapshot, resume
