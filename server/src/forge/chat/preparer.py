"""TurnPreparer: 一次 turn 启动前的所有 DB 工作。

职责:
    1. 解析 / 校验 / 新建 session
    2. 自动重命名判断
    3. 持久化 user 消息 + assistant 占位
    4. 把以上都打包成 frozen TurnContext, 后续阶段消费

异常:
    - session 不存在 / 无权访问 -> raise TurnPreparationError;
      TurnOrchestrator 捕获后转 SSE error 事件.
    - DB 错误穿透抛出.
"""

from __future__ import annotations

import asyncio
import logging

from forge.api.schemas.chat import ModelOptionsIn
from forge.chat.model_meta import DEFAULT_CONTEXT_WINDOW, resolve_context_window
from forge.chat.types import TurnContext
from forge.infrastructure.database.database import session_scope
from forge.infrastructure.database.repositories.chat_message_repo import ChatMessageRepository
from forge.infrastructure.database.repositories.chat_session_repo import ChatSessionRepository
from forge.llm.providers.base import ChatMessage
from forge.observability.tracing.tracer import span

logger = logging.getLogger(__name__)

# 当前服务端只保留 Web Chat 路径, 固定使用 chat profile。
_DEFAULT_MODE = "chat"
_DEFAULT_CONTEXT_WINDOW = DEFAULT_CONTEXT_WINDOW


class TurnPreparationError(Exception):
    """preparation 阶段不可恢复错误. Orchestrator 转 SSE error."""

    def __init__(self, message: str, code: str = "40000") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class TurnPreparer:
    """无状态. 每个 prepare() 自有 DB 事务."""

    async def prepare(
        self,
        *,
        user_id: str,
        user_name: str,
        session_id: str | None,
        message: str,
        trace_id: str,
        model_options: ModelOptionsIn | None = None,
        attachments: list | None = None,
        kb_ids: list[str] | None = None,
    ) -> TurnContext:
        """跑完所有 DB 准备工作, 返回 TurnContext.

        分段以避免在 DB 事务内做长耗时操作:
          段1 (事务): 解析/新建 session, 判定是否需要重命名, commit.
          段中 (无事务): 若需要 → 调 utility LLM 生成标题 (≤3s),
                        持有 DB 连接的窗口缩到 0.
          段2 (事务): 写 user_msg + assistant 占位, 必要时改名, commit.
        """
        with span(
            "chat.prepare",
            user_id=user_id,
            session_hint=session_id or "<new>",
            input_len=len(message or ""),
        ) as s:
            selected_kb_ids: tuple[str, ...] = ()
            # ---- 段1: 会话解析 / 重命名判定 ----
            async with session_scope() as db:
                sess_repo = ChatSessionRepository(db)
                msg_repo = ChatMessageRepository(db)

                is_new_session = session_id is None
                if is_new_session:
                    session = await sess_repo.create(user_id=user_id)
                else:
                    existing_session = await sess_repo.get_by_id(session_id or "")
                    if existing_session is None:
                        raise TurnPreparationError("会话不存在", code="40410")
                    if existing_session.user_id != user_id:
                        raise TurnPreparationError("无权访问该会话", code="40310")
                    session = existing_session
                session_id_actual = session.id

                if is_new_session:
                    should_rename = True
                else:
                    existing_count = await msg_repo.count_by_session(session_id_actual)
                    should_rename = existing_count == 0 and session.title == "新会话"

                selected_kb_ids = await _resolve_accessible_kb_ids(db, kb_ids, user_id)
                await db.commit()

            # ---- 段中 (无事务): LLM 生成标题, 不占 DB 连接 ----
            new_title: str | None = None
            if should_rename:
                new_title = await _make_title_with_utility_llm(message, model_options)

            # ---- 段2: 写消息 + 必要时改名 ----
            async with session_scope() as db:
                sess_repo = ChatSessionRepository(db)
                msg_repo = ChatMessageRepository(db)

                # 用户消息 token 数落库算一次 (供上下文组装热路径读, 免重复 tiktoken)
                from forge.context_mgmt.meter.token_meter import get_token_meter

                # 附件占位: 大段输入已被前端转为会话文件, 这里只把 [file:<id>] 引用
                # 拼到 user 消息尾部 (不内联全文), LLM 需要时用 read_file 按需读取。
                user_content = _append_file_placeholders(message, attachments)
                user_msg = await msg_repo.add(
                    session_id=session_id_actual,
                    role="user",
                    content=user_content,
                    status="done",
                    token_count=get_token_meter().count_text(user_content),
                )
                asst_msg = await msg_repo.add(
                    session_id=session_id_actual,
                    role="assistant",
                    content="",
                    status="streaming",
                    parent_id=user_msg.id,
                )
                # 绑定上传附件到该 user 消息 (校验归属), 供历史回看渲染附件卡片
                await _bind_attachments(
                    db, attachments, user_id, session_id_actual, user_msg.id
                )
                if new_title:
                    session_in_tx = await sess_repo.get_by_id(session_id_actual)
                    if session_in_tx is not None:
                        await sess_repo.update_title(session_in_tx, new_title)

                await db.commit()

                user_msg_id = user_msg.id
                assistant_msg_id = asst_msg.id

                s.set("session_id", session_id_actual)
                s.set("assistant_msg_id", assistant_msg_id)
                s.set("agent_mode", _DEFAULT_MODE)
                s.set("is_new_session", is_new_session)
                s.set("renamed", new_title is not None)

        model_options_dict = model_options.model_dump() if model_options else None
        # 真实 context_window 取自 model 配置, 失败回落到默认.
        # 影响 ContextManager 主动压缩的阈值判定.
        context_window = await resolve_context_window(
            model_options_dict, default=_DEFAULT_CONTEXT_WINDOW,
        )
        ctx = TurnContext(
            user_id=user_id,
            user_name=user_name,
            session_id=session_id_actual,
            assistant_msg_id=assistant_msg_id,
            user_msg_id=user_msg_id,
            current_user_message=user_content,
            agent_mode=_DEFAULT_MODE,
            is_new_session=is_new_session,
            new_title=new_title,
            trace_id=trace_id,
            model_options=model_options_dict,
            selected_kb_ids=selected_kb_ids,
            exclude_message_ids=(user_msg_id,),
            context_window=context_window,
        )
        logger.info(
            "对话准备完成 session=%s user=%s message_id=%s new_session=%s input_len=%d",
            session_id_actual,
            user_id,
            assistant_msg_id,
            is_new_session,
            len(message),
            extra={
                "session_id": session_id_actual,
                "user_id": user_id,
                "message_id": assistant_msg_id,
            },
        )
        return ctx


def _append_file_placeholders(message: str, attachments) -> str:
    """把附件文件引用拼到 user 消息尾部, 让 LLM 知道可用 read_file 读取 (不内联全文)。"""
    parts: list[str] = []
    for a in attachments or []:
        fid = getattr(a, "id", None) or getattr(a, "file_id", None)
        if not fid:
            continue
        name = getattr(a, "name", None) or ""
        parts.append(f"[file:{fid}{(' name=' + name) if name else ''}]")
    if not parts:
        return message
    return f"{message}\n\n" + "\n".join(parts)


async def _bind_attachments(
    db, attachments, user_id: str, session_id: str, message_id: str
) -> None:
    """把上传文件回填到会话与产生它的 user 消息 (校验 owner 归属)。

    上传文件在上传时与会话解耦 (session_id 为空), 这里发消息时才回填关系。
    """
    fids = [
        str(getattr(a, "id", None) or getattr(a, "file_id", None))
        for a in (attachments or [])
        if (getattr(a, "id", None) or getattr(a, "file_id", None))
    ]
    if not fids:
        return
    from forge.infrastructure.database.repositories.user_file_repo import (
        UserFileRepository,
    )

    repo = UserFileRepository(db)
    for fid in fids:
        await repo.bind_session_and_message(
            fid, session_id, message_id, owner_user_id=user_id
        )


async def _resolve_accessible_kb_ids(db, kb_ids: list[str] | None, user_id: str) -> tuple[str, ...]:
    cleaned = [str(kb_id).strip() for kb_id in (kb_ids or []) if str(kb_id).strip()]
    if not cleaned:
        return ()
    from forge.infrastructure.database.repositories.knowledge_base_repo import (
        KnowledgeBaseRepository,
    )

    repo = KnowledgeBaseRepository(db)
    kbs = await repo.find_accessible_by_ids(cleaned, user_id)
    accessible = {str(kb.id) for kb in kbs}
    return tuple(kb_id for kb_id in cleaned if kb_id in accessible)


def _make_title(text: str, max_len: int = 25) -> str:
    """用首条消息内容生成会话标题, 超长时在语义边界截断."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    for sep in ("，", "。", "！", "？", "、", ",", ".", "!", "?", " "):
        idx = truncated.rfind(sep)
        if idx > max_len // 2:
            return truncated[:idx].rstrip() + "…"
    return truncated.rstrip() + "…"


async def _make_title_with_utility_llm(text: str, model_options, max_len: int = 25) -> str:
    """优先用 utility 档位模型生成标题，失败时回落到本地截断。

    走 LLMGateway 的 utility 路径 (task_type="utility"), 享受完整 Pre/Post pipeline.
    """
    try:
        from forge.config.settings import get_settings
        from forge.llm import LLMRequest, get_llm_gateway

        settings = get_settings()
        provider = getattr(model_options, "provider", None)
        model = getattr(model_options, "model", None)
        if isinstance(model_options, dict):
            provider = model_options.get("provider")
            model = model_options.get("model")

        gateway = get_llm_gateway(settings)

        async def _call_title_llm() -> str:
            req = LLMRequest(
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "你是会话标题生成器。请根据用户首条消息生成一个中文短标题，"
                            "不超过 20 个字，不要加引号，不要解释。"
                        ),
                    ),
                    ChatMessage(role="user", content=text),
                ],
                temperature=0,
                max_tokens=32,
                task_type="utility",
                model_profile="fast",
                preferred_provider=provider,
                preferred_model=model,
                cache_enabled=True,
            )
            resp = await gateway.complete(req)
            return (resp.content or "").strip()

        title = await asyncio.wait_for(_call_title_llm(), timeout=3.0)
        title = title.strip().strip("\"'“”‘’")
        if title:
            return _make_title(title, max_len=max_len)
    except Exception:
        logger.exception("工具模型生成会话标题失败，回落到本地标题生成")
    return _make_title(text, max_len=max_len)
