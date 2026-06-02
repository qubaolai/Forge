"""Agent 抽象基类.

定义所有 agent 模式的公共接口.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from forge.core.types.message import Message


@dataclass
class AgentResult:
    """Agent 执行结果."""

    output: str
    messages: list[Message] = field(default_factory=list)
    steps: int = 0
    usage: dict = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentEvent:
    """Agent 流式事件.

    type 对齐前端 SSEEvent:
        message_start / tool_call / tool_result / delta / done / error
    """

    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}


class BaseAgent(ABC):
    @abstractmethod
    async def run(self, user_input: str, *, history: list[Message] | None = None) -> AgentResult: ...

    def stream(
        self,
        user_input: str,
        *,
        history: list[Message] | None = None,
        message_id: str | None = None,
        session_id: str | None = None,
        abort_event: Any | None = None,
    ) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError(f"{type(self).__name__} does not support stream()")
