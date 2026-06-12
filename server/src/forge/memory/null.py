"""空实现: 关闭记忆系统时的占位.

让 ContextBuilder 在 stage 1 / 测试 / 单元环境下不依赖真实存储就能运行.
所有读方法返回空, 不抛异常, 不写任何状态.
"""

from __future__ import annotations

from forge.memory.base import Fact, FactRecallRequest, MemoryStore, Summary


class NullMemoryStore(MemoryStore):
    """MemoryStore ABC 的空实现."""

    async def get_summary(self, session_id: str) -> Summary | None:
        return None

    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        return []
