"""事实写入冲突解决策略.

钩子点: FactStore.write() 内部, "拿到 LLM 抽取的新事实" 与 "DB 里相似的已有
事实" 比对时调用.

返回值是个 ADT (Insert | Replace | Merge | Skip):
    Store 用 isinstance / match 分发到具体 SQL 行为. 比 Enum + 多个可空字段
    干净, 且类型检查器能帮你穷尽分支.

Stage 2 只装 NoOpConflictResolver (永远 Insert, 不去重). Stage 3+ 视需求加
LatestWinsConflictResolver / LLMJudgeConflictResolver, 继承同一 ABC 即可,
Store 调用方零改动.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forge.memory.base import Fact
from forge.memory.scope import MemoryScope


# ---------------------------------------------------------------------------
# Resolution ADT: Store 据此决定 INSERT / UPDATE / 跳过
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Insert:
    """新增一条事实."""

    content: str


@dataclass(frozen=True)
class Replace:
    """用新内容整体替换某条已有事实 (UPDATE)."""

    target_id: str
    content: str


@dataclass(frozen=True)
class Merge:
    """把新事实合并进某条已有事实, content 是 resolver 算出的合并结果."""

    target_id: str
    content: str


@dataclass(frozen=True)
class Skip:
    """放弃写入. reason 仅用于日志/可观测, Store 不应据此分支."""

    reason: str = ""


Resolution = Insert | Replace | Merge | Skip


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------
class ConflictResolver(ABC):
    """决定 "新事实 vs 已有相似事实" 该怎么处理.

    Note:
        async 是为了给 LLMJudgeConflictResolver 留口子 (要调 LLM).
        NoOp / 纯规则实现也走 async, 调用方统一 await.
    """

    @abstractmethod
    async def resolve(
        self,
        scope: MemoryScope,
        new_fact: Fact,
        similar_existing: list[Fact],
    ) -> Resolution:
        """
        Args:
            scope: 当前作用域 (理论上 similar_existing 已按此过滤, 这里再传一遍
                只是方便 resolver 做额外判断).
            new_fact: 待写入的新事实, id 可能尚未生成.
            similar_existing: 由 Store 预先按语义相似度召回的 top-k 已有事实.
                空列表表示无相似项 -- 此时通常应返回 Insert.

        Returns:
            Resolution ADT 之一.
        """
        ...


# ---------------------------------------------------------------------------
# NoOp 实现: Stage 2 默认
# ---------------------------------------------------------------------------
class NoOpConflictResolver(ConflictResolver):
    """永远 Insert, 不去重. 让重复内容靠 DB 唯一约束兜底 (若有)."""

    async def resolve(
        self,
        scope: MemoryScope,
        new_fact: Fact,
        similar_existing: list[Fact],
    ) -> Resolution:
        return Insert(content=new_fact.content)


# ---------------------------------------------------------------------------
# 阈值去重: Stage 3 (事实层) 默认
# ---------------------------------------------------------------------------
class ThresholdDedupResolver(ConflictResolver):
    """相似度阈值去重: 已有事实里最高相似分 >= 阈值则 Skip, 否则 Insert.

    similar_existing 由 Store 预先按语义召回, Fact.score 即余弦相似度.
    纯规则、零 LLM 成本; 语义级冲突合并 (LLMJudgeConflictResolver) 留作后续方向.
    """

    def __init__(self, dedup_threshold: float = 0.92) -> None:
        self._threshold = dedup_threshold

    async def resolve(
        self,
        scope: MemoryScope,
        new_fact: Fact,
        similar_existing: list[Fact],
    ) -> Resolution:
        if similar_existing:
            top = max(similar_existing, key=lambda f: f.score)
            if top.score >= self._threshold:
                return Skip(reason=f"dup>={self._threshold:.2f} (top={top.score:.3f})")
        return Insert(content=new_fact.content)
