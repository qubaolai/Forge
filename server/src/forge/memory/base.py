"""记忆系统的对外契约: ABC + 数据类型.

MemoryStore 是 ContextBuilder 的依赖: 提供 "取摘要" 和 "召回事实" 两个读接口.
写接口 (保存摘要、写入事实) 由后台任务直接调用具体 Store (SummaryStore /
FactStore), 不走这个 ABC -- 避免抽象膨胀成 "什么都有的大接口".

设计原则:
    - ContextBuilder 视 MemoryStore 为 best-effort:
      失败 raise MemoryStoreError, ContextBuilder 捕获后降级 + 记录到
      BuildMeta.degraded.
    - 空结果 (None / []) 表示 "真的没有", 不是错误.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# ---------------------------------------------------------------------------
# 1. 数据类型
# ---------------------------------------------------------------------------
FactSource = Literal["llm_extracted", "user_manual"]
# 两种来源都支持:
#   llm_extracted: 对话结束后 LLM 自动抽取
#   user_manual:   用户在设置里手动添加偏好


@dataclass
class Summary:
    """会话级摘要 (session-scoped)."""

    session_id: str
    content: str  # 摘要正文
    covered_until_message_id: str | None  # 摘要覆盖到哪条消息 (再往后是原文 history)
    token_count: int  # 摘要本身的 token 估算
    updated_at: datetime
    workspace_id: str | None = None
    version: int = 1  # 每次重生成 +1


@dataclass
class Fact:
    """用户级长期事实 (user-scoped)."""

    id: str
    user_id: str
    content: str  # "用户偏好 Python"
    source: FactSource
    score: float = 0.0  # recall 返回时填 (语义相似度); 写入时忽略
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FactRecallRequest:
    user_id: str
    query: str  # 一般是 current_user_message
    top_k: int = 5
    min_score: float = 0.0  # 低于此分的过滤掉


# ---------------------------------------------------------------------------
# 2. 异常: 与 "空结果" 区分
# ---------------------------------------------------------------------------
class MemoryStoreError(Exception):
    """记忆存储读取失败 (DB 挂、向量库超时等).

    ContextBuilder 捕获后降级 + 写 BuildMeta.degraded, 不向上抛.
    """


# ---------------------------------------------------------------------------
# 3. ABC: ContextBuilder 只用这两个读方法
# ---------------------------------------------------------------------------
class MemoryStore(ABC):
    """无状态. 同一实例可并发处理多个请求."""

    @abstractmethod
    async def get_summary(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None:
        """取该 session 当前最新摘要.

        Returns:
            Summary 或 None (该 session 还没生成过摘要).

        Raises:
            MemoryStoreError: 读取失败.
        """
        ...

    @abstractmethod
    async def recall_facts(self, request: FactRecallRequest) -> list[Fact]:
        """按语义召回用户长期事实.

        Returns:
            按 score 降序的 Fact 列表; 无结果返回 [].

        Raises:
            MemoryStoreError: 读取失败.
        """
        ...
