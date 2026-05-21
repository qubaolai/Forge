"""TurnPreparer: 一次 turn 启动前的所有 DB 工作.

职责:
    1. 解析 / 校验 / 新建 session
    2. 加载 agent + 校验 mode
    3. 自动重命名判断
    4. 持久化 user 消息 + assistant 占位
    5. 把以上都打包成 frozen TurnContext, 后续阶段消费

异常:
    - session 不存在 / 无权访问 / mode 不支持 -> raise TurnPreparationError;
      TurnOrchestrator 捕获后转 SSE error 事件.
    - DB 错误穿透抛出.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from forge.chat.types import TurnContext
from forge.infrastructure.database.database import get_session_factory
from forge.infrastructure.database.repositories.agent_repo import (
    AgentRepository,
)

# Storage protocol injected, swap by deployment_mode (S6.5 M3).
from forge.infrastructure.storage import make_message_store, make_session_store
from forge.observability.tracing.tracer import span

logger = logging.getLogger(__name__)

_SUPPORTED_MODES = {"react"}  # PR R1 暂时一个; 多 mode 加注册表后扩展


class TurnPreparationError(Exception):
    """preparation 阶段不可恢复错误. Orchestrator 转 SSE error."""

    def __init__(self, message: str, code: str = "40000") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class _AgentSnapshot:
    """从 AgentOrm 摘出 Preparer / Assembler 需要的字段, 避免 ORM 反向依赖."""

    agent_id: str | None
    mode: str
    name: str | None
    system_prompt: str
    model_id: str | None
    context_window: int


class TurnPreparer:
    """无状态. session_factory 在构造时注入, 每个 prepare() 自有 DB 事务."""

    def __init__(self, supported_modes: set[str] = _SUPPORTED_MODES) -> None:
        self._supported_modes = supported_modes

    async def prepare(
        self,
        *,
        user_id: str,
        user_name: str,
        session_id: str | None,
        message: str,
        agent_id_hint: str | None,
        trace_id: str,
        model_options: dict | None = None,
    ) -> tuple[TurnContext, _AgentSnapshot]:
        """跑完所有 DB 准备工作, 返回 (TurnContext, AgentSnapshot).

        AgentSnapshot 单独返回是给 ContextAssembler / Runner 用 (system_prompt / model_id / name).
        TurnContext 是后续步骤的统一上下文, 不暴露 ORM.
        """
        factory = get_session_factory()
        with span(
            "chat.prepare",
            user_id=user_id,
            session_hint=session_id or "<new>",
            input_len=len(message or ""),
        ) as s:
            async with factory() as db:
                sess_repo = make_session_store(db)
                agent_repo = AgentRepository(db)  # AgentStore Protocol 未列入 S6.5
                msg_repo = make_message_store(db)

                # 1. session 解析 / 新建
                is_new_session = session_id is None
                if is_new_session:
                    agent_id = agent_id_hint or "default"
                    session = await sess_repo.create(user_id=user_id, agent_id=agent_id)
                else:
                    existing_session = await sess_repo.get_by_id(session_id or "")
                    if existing_session is None:
                        raise TurnPreparationError("会话不存在", code="40410")
                    session = existing_session
                session_id_actual = session.id

                # 2. agent + mode 守卫
                agent_orm = None
                if session.agent_id and session.agent_id != "default":
                    agent_orm = await agent_repo.get_by_id(session.agent_id)
                mode = getattr(agent_orm, "mode", None) or "react"
                if mode not in self._supported_modes:
                    raise TurnPreparationError(
                        f"暂不支持 agent.mode={mode!r}, 当前仅实现 {sorted(self._supported_modes)}",
                        code="50301",
                    )

                # 3. 自动重命名
                if is_new_session:
                    should_rename = True
                else:
                    existing_count = await msg_repo.count_by_session(session_id_actual)
                    should_rename = existing_count == 0 and session.title == "新会话"

                # 4. 持久化 user 消息
                user_msg = await msg_repo.add(
                    session_id=session_id_actual,
                    role="user",
                    content=message,
                    status="done",
                )

                # 5. 占位 assistant
                asst_msg = await msg_repo.add(
                    session_id=session_id_actual,
                    role="assistant",
                    content="",
                    status="streaming",
                    parent_id=user_msg.id,
                )

                # 6. 重命名 (与消息一起原子提交)
                new_title: str | None = None
                if should_rename:
                    new_title = _make_title(message)
                    await sess_repo.update_title(session, new_title)

                await db.commit()

                user_msg_id = user_msg.id
                assistant_msg_id = asst_msg.id

                s.set("session_id", session_id_actual)
                s.set("assistant_msg_id", assistant_msg_id)
                s.set("agent_mode", mode)
                s.set("is_new_session", is_new_session)
                s.set("renamed", new_title is not None)

        snapshot = _AgentSnapshot(
            agent_id=session.agent_id if session.agent_id != "default" else None,
            mode=mode,
            name=getattr(agent_orm, "name", None),
            system_prompt=(getattr(agent_orm, "system_prompt", "") or ""),
            model_id=getattr(agent_orm, "model_id", None),
            context_window=getattr(agent_orm, "context_window", 128_000),
        )
        ctx = TurnContext(
            user_id=user_id,
            user_name=user_name,
            session_id=session_id_actual,
            assistant_msg_id=assistant_msg_id,
            user_msg_id=user_msg_id,
            current_user_message=message,
            agent_id=snapshot.agent_id,
            agent_mode=snapshot.mode,
            is_new_session=is_new_session,
            new_title=new_title,
            trace_id=trace_id,
            model_options=model_options,
            exclude_message_ids=(user_msg_id,),
            context_window=snapshot.context_window,
        )
        logger.info(
            "对话准备完成 session=%s user=%s agent=%s message_id=%s new_session=%s "
            "input_len=%d agent_mode=%s",
            session_id_actual,
            user_id,
            snapshot.agent_id or "default",
            assistant_msg_id,
            is_new_session,
            len(message),
            mode,
            extra={
                "session_id": session_id_actual,
                "user_id": user_id,
                "message_id": assistant_msg_id,
                "agent_id": snapshot.agent_id,
            },
        )
        return ctx, snapshot


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
