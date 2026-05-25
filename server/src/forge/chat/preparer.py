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

import logging
import asyncio

from forge.chat.types import TurnContext
from forge.infrastructure.database.database import get_session_factory

from forge.infrastructure.database.repositories.chat_message_repo import ChatMessageRepository
from forge.infrastructure.database.repositories.chat_session_repo import ChatSessionRepository
from forge.observability.tracing.tracer import span
from forge.api.schemas.chat import ModelOptionsIn
from forge.llm.providers.base import ChatMessage

logger = logging.getLogger(__name__)

# 当前只支持 react 模式，后续 CLI plan 模式通过 runner 注册表扩展
_DEFAULT_MODE = "react"
_DEFAULT_CONTEXT_WINDOW = 128_000


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
        agent_id_hint: str | None,
        trace_id: str,
        model_options: ModelOptionsIn | None = None,
    ) -> TurnContext:
        """跑完所有 DB 准备工作, 返回 TurnContext."""
        factory = get_session_factory()
        with span(
            "chat.prepare",
            user_id=user_id,
            session_hint=session_id or "<new>",
            input_len=len(message or ""),
        ) as s:
            async with factory() as db:
                sess_repo = ChatSessionRepository(db)
                msg_repo = ChatMessageRepository(db)

                # 1. session 解析 / 新建
                is_new_session = session_id is None
                if is_new_session:
                    session = await sess_repo.create(user_id=user_id, agent_id="default")
                else:
                    existing_session = await sess_repo.get_by_id(session_id or "")
                    if existing_session is None:
                        raise TurnPreparationError("会话不存在", code="40410")
                    session = existing_session
                session_id_actual = session.id

                # 2. 自动重命名
                if is_new_session:
                    should_rename = True
                else:
                    existing_count = await msg_repo.count_by_session(session_id_actual)
                    should_rename = existing_count == 0 and session.title == "新会话"

                # 3. 持久化 user 消息
                user_msg = await msg_repo.add(
                    session_id=session_id_actual,
                    role="user",
                    content=message,
                    status="done",
                )

                # 4. 占位 assistant
                asst_msg = await msg_repo.add(
                    session_id=session_id_actual,
                    role="assistant",
                    content="",
                    status="streaming",
                    parent_id=user_msg.id,
                )

                # 5. 重命名 (与消息一起原子提交)
                new_title: str | None = None
                if should_rename:
                    new_title = await _make_title_with_utility_llm(message, model_options)
                    await sess_repo.update_title(session, new_title)

                await db.commit()

                user_msg_id = user_msg.id
                assistant_msg_id = asst_msg.id

                s.set("session_id", session_id_actual)
                s.set("assistant_msg_id", assistant_msg_id)
                s.set("agent_mode", _DEFAULT_MODE)
                s.set("is_new_session", is_new_session)
                s.set("renamed", new_title is not None)

        ctx = TurnContext(
            user_id=user_id,
            user_name=user_name,
            session_id=session_id_actual,
            assistant_msg_id=assistant_msg_id,
            user_msg_id=user_msg_id,
            current_user_message=message,
            agent_id=None,
            agent_mode=_DEFAULT_MODE,
            is_new_session=is_new_session,
            new_title=new_title,
            trace_id=trace_id,
            model_options=model_options.model_dump() if model_options else None,
            exclude_message_ids=(user_msg_id,),
            context_window=_DEFAULT_CONTEXT_WINDOW,
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
    """优先用工具模型生成标题，失败时回落到本地截断。"""
    try:
        from forge.config.settings import get_settings
        from forge.llm.gateway import build_utility_chain_from_settings

        settings = get_settings()
        provider = getattr(model_options, "provider", None)
        model = getattr(model_options, "model", None)
        if isinstance(model_options, dict):
            provider = model_options.get("provider")
            model = model_options.get("model")

        chain = await build_utility_chain_from_settings(
            settings,
            provider=provider,
            model=model,
        )

        async def _call_title_llm() -> str:
            result = await chain.chat(
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "你是会话标题生成器。请根据用户首条消息生成一个中文短标题，"
                            "不超过 20 个字，不要加引号，不要解释。"
                        ),
                    ),
                    ChatMessage(role="user", content=text),
                ],
                temperature=0.2,
                max_tokens=32,
            )
            return (result.content or "").strip()
        title = await asyncio.wait_for(_call_title_llm(), timeout=3.0)
        title = title.strip().strip("\"'“”‘’")
        if title:
            return _make_title(title, max_len=max_len)
    except Exception:
        logger.exception("工具模型生成会话标题失败，回落到本地标题生成")
    return _make_title(text, max_len=max_len)
