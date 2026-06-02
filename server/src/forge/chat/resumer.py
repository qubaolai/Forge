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
from typing import Any

from forge.chat.model_meta import DEFAULT_CONTEXT_WINDOW, resolve_context_window
from forge.chat.types import ResumeState, TurnContext
from forge.infrastructure.database.database import get_session_factory
from forge.infrastructure.database.repositories.chat_message_repo import ChatMessageRepository
from forge.infrastructure.database.repositories.chat_session_repo import ChatSessionRepository
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


def _is_message_active_locally(message_id: str) -> bool:
    """检查进程内是否有该 message_id 的活跃流。

    延迟导入避免循环依赖（orchestrator 已 import resumer）。
    服务重启后进程内无任何活跃流，返回 False，允许接管遗留 streaming 消息。
    """
    try:
        from forge.chat.supervisor import get_chat_supervisor  # noqa: PLC0415

        return get_chat_supervisor().has_active(message_id)
    except ImportError:
        return False


async def _rebuild_from_event_log(message_id: str) -> dict | None:
    """从 events.jsonl 折叠重建 prev_content / prev_tool_calls / prev_usage 等。

    用于服务重启 / DB 内容缺失场景: events.jsonl 是 source of truth, DB content
    只是 finalizer 折叠后的最终态; 若 turn 未走到 finalizer (例如崩溃), DB content
    可能空, 但 events.jsonl 里所有已下发的 delta 都在.

    返回 None 表示该 message 没有 events.jsonl (新对话 / 已清理).
    """
    try:
        from forge.chat.event_store import ChatEventStore  # noqa: PLC0415
    except ImportError:
        return None

    try:
        store = ChatEventStore(message_id)
    except OSError as exc:
        logger.warning("读取续写事件日志失败 message_id=%s: %s", message_id, exc)
        return None
    if not store.dir.exists():
        return None

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_duration_ms: int | None = None
    tool_calls_by_id: dict[str, dict] = {}
    usage: dict = {}
    finish_reason: str | None = None
    has_events = False

    async for record in store.iter_events(after_seq=0):
        has_events = True
        ev_type = record.get("type")
        if ev_type == "delta":
            content_parts.append(record.get("content") or "")
        elif ev_type == "reasoning_delta":
            reasoning_parts.append(record.get("content") or "")
        elif ev_type == "reasoning_end":
            ms = record.get("reasoning_duration_ms")
            if isinstance(ms, int | float):
                reasoning_duration_ms = int(ms)
        elif ev_type == "tool_call":
            tc = record.get("tool_call")
            if isinstance(tc, dict) and tc.get("id"):
                tool_calls_by_id[tc["id"]] = dict(tc)
        elif ev_type == "tool_result":
            tid = record.get("tool_call_id")
            if tid and tid in tool_calls_by_id:
                tool_calls_by_id[tid]["status"] = record.get("status")
                tool_calls_by_id[tid]["result"] = record.get("result")
        elif ev_type in ("done", "task_partial"):
            u = record.get("usage")
            if isinstance(u, dict):
                usage = dict(u)
            finish_reason = record.get("finish_reason") or record.get("reason")
            ms = record.get("reasoning_duration_ms")
            if isinstance(ms, int | float):
                reasoning_duration_ms = int(ms)

    if not has_events:
        return None

    return {
        "content": "".join(content_parts),
        "reasoning_content": "".join(reasoning_parts) or None,
        "reasoning_duration_ms": reasoning_duration_ms,
        "tool_calls": list(tool_calls_by_id.values()),
        "usage": usage,
        "finish_reason": finish_reason,
    }


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
    ) -> tuple[TurnContext, ResumeState]:
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
                msg_repo = ChatMessageRepository(db)
                sess_repo = ChatSessionRepository(db)

                asst = await msg_repo.get_by_id(message_id)
                if asst is None or asst.role != "assistant":
                    raise ResumeError("assistant 消息不存在", code="40440")

                session = await sess_repo.get_by_id(asst.session_id)
                if session is None or session.user_id != user_id:
                    raise ResumeError("无权访问该会话", code="40310")

                if asst.status == "streaming":
                    # 并发 resume 防护：本地有活跃流则拒绝
                    if _is_message_active_locally(asst.id):
                        raise ResumeError(
                            "已有活跃流正在生成中，不允许并发 resume",
                            code="40902",
                        )
                    # 遗留 streaming（服务重启/异常中断）→ 视同 aborted 允许续写
                elif asst.status not in _RESUMABLE_STATUSES:
                    raise ResumeError(
                        f"当前状态 {asst.status!r} 不可恢复 (仅支持 aborted / partial)",
                        code="40901",
                    )

                if asst.parent_id is None:
                    raise ResumeError("找不到原始用户消息 (parent_id 缺失)", code="40441")
                parent = await msg_repo.get_by_id(asst.parent_id)
                if parent is None or parent.role != "user":
                    raise ResumeError("原始用户消息已删除", code="40441")

                # agent 已废弃，使用默认配置
                mode = "react"

                # 先在状态被改之前抓快照 (update 之后 asst.status 会变成 "streaming")
                session_id = session.id
                assistant_msg_id = asst.id
                user_msg_id = parent.id
                user_msg_content = parent.content or ""
                db_content = asst.content or ""
                db_tool_calls = list(asst.tool_calls or [])
                db_reasoning = asst.reasoning_content
                db_reasoning_ms = asst.reasoning_duration_ms
                db_usage = dict(asst.usage or {})
                db_model_options = _extract_model_options(asst.context_meta or {})
                # 遗留 streaming → 归一化为 aborted（语义等价，便于日志/finalizer 处理）
                prev_status = "aborted" if asst.status == "streaming" else asst.status
                prev_finish_reason = (asst.context_meta or {}).get("finish_reason", prev_status)

                # ★ 关键: 状态置回 streaming, 让本轮可以再注册 abort_event,
                # 并防止并发 resume 互相覆盖.
                await msg_repo.update(asst, status="streaming")
                await db.commit()

            # ★ 新架构: events.jsonl 才是 source of truth.
            # 若 DB content 为空 (旧 bug 残留 / finalizer 没跑) 但 events.jsonl 还在,
            # 折叠日志重建 prev_* 才能让续写真正能"继续"而不是"重来".
            prev_content = db_content
            prev_tool_calls = db_tool_calls
            prev_reasoning = db_reasoning
            prev_reasoning_ms = db_reasoning_ms
            prev_usage = db_usage
            rebuilt = await _rebuild_from_event_log(assistant_msg_id)
            if rebuilt is not None:
                rebuilt_content = rebuilt["content"]
                if len(rebuilt_content) >= len(db_content):
                    prev_content = rebuilt_content
                    prev_tool_calls = rebuilt["tool_calls"] or db_tool_calls
                    if rebuilt["reasoning_content"]:
                        prev_reasoning = rebuilt["reasoning_content"]
                    if rebuilt["reasoning_duration_ms"] is not None:
                        prev_reasoning_ms = rebuilt["reasoning_duration_ms"]
                    if rebuilt["usage"]:
                        prev_usage = rebuilt["usage"]
                    if rebuilt["finish_reason"]:
                        prev_finish_reason = rebuilt["finish_reason"]
                    logger.info(
                        "续写从 events.jsonl 重建 prev_content message_id=%s "
                        "db_len=%d jsonl_len=%d",
                        assistant_msg_id, len(db_content), len(rebuilt_content),
                    )

            s.set("session_id", session_id)
            s.set("prev_status", prev_status)
            s.set("prev_content_len", len(prev_content))
            s.set("prev_tool_calls", len(prev_tool_calls))
            s.set("agent_mode", mode)

        # 渲染续写提示词: 含 suffix_anchor (上次末尾 80 字符) + 结构感知 (代码块/表格)
        resume_prompt = _render_resume_prompt(prev_content)

        # 用与首轮一致的 context_window 解析逻辑, 续写过程中阈值判定才不会跳变.
        context_window = await resolve_context_window(
            db_model_options, default=DEFAULT_CONTEXT_WINDOW,
        )
        ctx = TurnContext(
            user_id=user_id,
            user_name="",  # resume 时不再用 user_name (system 已渲染过), 留空
            session_id=session_id,
            assistant_msg_id=assistant_msg_id,
            user_msg_id=user_msg_id,
            current_user_message=resume_prompt,
            agent_mode="chat",
            is_new_session=False,
            new_title=None,
            trace_id=trace_id,
            model_options=db_model_options,
            exclude_message_ids=(assistant_msg_id,),
            context_window=context_window,
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
        return ctx, resume


def _extract_model_options(context_meta: dict) -> dict | None:
    """从消息元信息恢复上轮模型选项。"""
    raw = context_meta.get("model_options")
    if not isinstance(raw, dict):
        return None
    provider = raw.get("provider")
    model = raw.get("model")
    if not isinstance(provider, str) or not isinstance(model, str):
        return None
    out: dict[str, Any] = {"provider": provider, "model": model}
    if "thinking" in raw:
        out["thinking"] = raw.get("thinking")
    if isinstance(raw.get("thinking_level"), str):
        out["thinking_level"] = raw.get("thinking_level")
    return out
