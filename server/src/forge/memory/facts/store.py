"""FactStore: 用户长期事实的持久化层 (user 级跨会话记忆).

设计:
    - 镜像 SummaryStore 风格: 注入 async_sessionmaker, 每方法自开 session;
      但写法用 select-then-write (跨方言, SQLite 集成测试可跑), 不用 MySQL 方言 upsert.
    - 所有 API 强制吃 MemoryScope, 在签名层阻止 "忘了按 user 过滤" 的越权 bug.
    - 向量召回: 按 user_id 拉全量 -> 只取 model 匹配当前 embedder 的行 ->
      暴力余弦 (int8 量化值直接算, 对称量化下 cosine 与 scale 无关) ->
      ForgettingPolicy.is_alive 过滤 -> min_score 过滤 -> top_k 降序.
      单用户事实量级小 (百级), 暴力扫优于引入向量库.
    - 降级: embedder 不可用 / 无 model 匹配向量 (管理员换了 embedding 模型) ->
      回退按 updated_at 降序取 top_k, score=0 —— 召回不致全盲;
      旧向量按 "缓存未命中" 处理不混用, 重 embed 回填留作后续方向.
    - 写路径: 新事实先做一次内部相似召回, 交 ConflictResolver 决策
      (Insert / Replace / Merge / Skip), Store 按 ADT 分发 SQL 行为.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.infrastructure.database.orm.user_fact_orm import UserFactOrm
from forge.infrastructure.storage.data_protocols import FactStore as FactStoreBase
from forge.memory.base import Fact, FactRecallRequest, FactSource, MemoryStoreError
from forge.memory.policies.conflict import (
    ConflictResolver,
    Insert,
    Merge,
    Replace,
    Skip,
)
from forge.memory.policies.forgetting import ForgettingPolicy
from forge.memory.scope import MemoryScope
from forge.utils.vector import cosine_similarity, dequantize_int8, quantize_int8

logger = logging.getLogger(__name__)

# 异步 embedder 解析器: 返回 embedder 实例或 None (不可用).
# 镜像 context_mgmt.recall 的 BoundModelResolver 用法, 以 callable 注入避免硬依赖.
EmbedderResolver = Callable[[], Awaitable[Any]]


def _to_int(value: str | int | None) -> int | None:
    """对外 ID (str(雪花)) → BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class FactStore(FactStoreBase):
    """用户长期事实持久化. 长寿单例, 并发安全 (每方法自有 session)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        conflict_resolver: ConflictResolver,
        forgetting_policy: ForgettingPolicy,
        embedder_resolver: EmbedderResolver | None = None,
        min_score: float = 0.5,
        top_k_cap: int = 20,
    ) -> None:
        self._factory = session_factory
        self._resolver = conflict_resolver
        self._forgetting = forgetting_policy
        self._embedder_resolver = embedder_resolver
        self._min_score = min_score
        self._top_k_cap = top_k_cap

    # ------------------------------------------------------------------
    # 写: 相似召回 -> ConflictResolver 决策 -> 按 ADT 分发 SQL
    # ------------------------------------------------------------------
    async def write(
        self,
        scope: MemoryScope,
        *,
        content: str,
        source: FactSource = "llm_extracted",
        source_session_id: str | None = None,
    ) -> Fact | None:
        """写入一条事实, 经 ConflictResolver 去重/合并.

        Returns:
            Fact: 落库后的事实 (Insert/Replace/Merge).
            None: 内容为空 或 resolver 判 Skip.

        Raises:
            MemoryStoreError: DB 写入失败.
        """
        content = content.strip()
        if not content:
            return None

        # 1. 算向量 (embedder 不可用时降级为无向量, 仍可写入)
        vector, model, dim = await self._embed(content)

        # 2. 相似召回 (给 resolver 决策用); 无向量时无从比对, 走空列表
        similar: list[Fact] = []
        if vector:
            try:
                _, similar = await self._recall_by_vector(
                    scope.user_id, vector, model, top_k=5, min_score=0.0
                )
            except MemoryStoreError:
                logger.warning("写入前相似召回失败 user=%s, 按无相似处理", scope.user_id)

        new_fact = Fact(
            id="",
            user_id=scope.user_id,
            content=content,
            source=source,
            source_session_id=source_session_id,
        )
        resolution = await self._resolver.resolve(scope, new_fact, similar)

        # 3. 按 ADT 分发
        if isinstance(resolution, Skip):
            logger.info(
                "事实写入跳过 user=%s reason=%s content=%.50s",
                scope.user_id,
                resolution.reason,
                content,
            )
            return None

        try:
            async with self._factory() as db:
                if isinstance(resolution, Insert):
                    row = UserFactOrm(
                        user_id=_to_int(scope.user_id),
                        content=resolution.content,
                        source=source,
                        source_session_id=_to_int(source_session_id),
                        vector_i8=quantize_int8(vector) if vector else None,
                        model=model,
                        dim=dim,
                    )
                    db.add(row)
                elif isinstance(resolution, Replace | Merge):
                    target = (
                        await db.execute(
                            select(UserFactOrm).where(
                                UserFactOrm.id == _to_int(resolution.target_id),
                                UserFactOrm.user_id == _to_int(scope.user_id),
                            )
                        )
                    ).scalar_one_or_none()
                    if target is None:
                        logger.warning(
                            "Replace/Merge 目标事实不存在 user=%s target=%s, 退化为 Insert",
                            scope.user_id,
                            resolution.target_id,
                        )
                        row = UserFactOrm(
                            user_id=_to_int(scope.user_id),
                            content=resolution.content,
                            source=source,
                            source_session_id=_to_int(source_session_id),
                            vector_i8=quantize_int8(vector) if vector else None,
                            model=model,
                            dim=dim,
                        )
                        db.add(row)
                    else:
                        # 合并/替换后内容变了, 向量须重算; resolver 产出内容与新事实
                        # 不同时 (Merge), 用新内容向量近似 —— 后续重 embed 回填修正.
                        target.content = resolution.content
                        target.vector_i8 = quantize_int8(vector) if vector else None
                        target.model = model
                        target.dim = dim
                        row = target
                else:  # pragma: no cover - ADT 穷尽兜底
                    raise MemoryStoreError(f"未知 Resolution 类型: {resolution!r}")
                await db.commit()
                await db.refresh(row)
                return _orm_to_fact(row)
        except SQLAlchemyError as exc:
            logger.warning("FactStore.write 失败 user=%s: %s", scope.user_id, exc)
            raise MemoryStoreError(f"FactStore.write failed: {exc}") from exc

    # ------------------------------------------------------------------
    # 读: 语义召回 (MemoryStore.recall_facts 的真实现)
    # ------------------------------------------------------------------
    async def recall(self, request: FactRecallRequest) -> list[Fact]:
        """按语义召回用户事实; embedder 不可用时回退 recency.

        Raises:
            MemoryStoreError: DB 读取失败.
        """
        query = (request.query or "").strip()
        effective_min = max(self._min_score, request.min_score)
        top_k = min(request.top_k, self._top_k_cap)
        if top_k <= 0:
            return []

        query_vec: list[float] = []
        model = ""
        if query:
            embedder = await self._resolve_embedder()
            if embedder is not None:
                model = _embedder_model(embedder)
                try:
                    raw = await asyncio.to_thread(embedder.embed_query, query)
                    query_vec = _as_vector(raw)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("事实召回 query embedding 失败, 回退 recency: %s", exc)

        if query_vec:
            candidates, facts = await self._recall_by_vector(
                request.user_id, query_vec, model, top_k=top_k, min_score=effective_min
            )
            if candidates > 0:
                # 有 model 匹配的候选行: 即使全被 min_score 过滤也按 "确无相关" 返回,
                # 不能回退 recency (否则等于绕过相似度门槛, 把不相关事实塞进上下文)
                return facts
        # embedder 不可用 / 无 model 匹配向量行 (如换过 embedding 模型) -> recency 兜底
        return await self._recall_by_recency(request.user_id, top_k)

    async def _recall_by_vector(
        self,
        user_id: str,
        query_vec: list[float],
        model: str,
        *,
        top_k: int,
        min_score: float,
    ) -> tuple[int, list[Fact]]:
        """按 user 全量拉 model 匹配的向量行, 暴力余弦取 top_k.

        Returns:
            (候选行数, 过滤后的事实列表): 候选数用于区分 "确无相关" (有候选但
            全低分, 返回空即可) 与 "无从比对" (零候选, 调用方回退 recency).
        """
        try:
            async with self._factory() as db:
                rows = (
                    (
                        await db.execute(
                            select(UserFactOrm).where(
                                UserFactOrm.user_id == _to_int(user_id),
                                UserFactOrm.model == model,
                                UserFactOrm.vector_i8.is_not(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        except SQLAlchemyError as exc:
            logger.warning("FactStore 向量召回失败 user=%s: %s", user_id, exc)
            raise MemoryStoreError(f"FactStore.recall failed: {exc}") from exc

        now = datetime.utcnow()
        candidates = 0
        scored: list[Fact] = []
        for row in rows:
            vec = dequantize_int8(row.vector_i8)
            if not vec or len(vec) != len(query_vec):
                continue
            candidates += 1
            fact = _orm_to_fact(row)
            if not self._forgetting.is_alive(fact, now):
                continue
            fact.score = cosine_similarity(query_vec, vec)
            if fact.score >= min_score:
                scored.append(fact)
        scored.sort(key=lambda f: f.score, reverse=True)
        return candidates, scored[:top_k]

    async def _recall_by_recency(self, user_id: str, top_k: int) -> list[Fact]:
        """降级路径: 按 updated_at 降序取 top_k, score=0."""
        try:
            async with self._factory() as db:
                rows = (
                    (
                        await db.execute(
                            select(UserFactOrm)
                            .where(UserFactOrm.user_id == _to_int(user_id))
                            .order_by(UserFactOrm.updated_at.desc(), UserFactOrm.id.desc())
                            .limit(top_k * 2)  # 留余量给 is_alive 过滤
                        )
                    )
                    .scalars()
                    .all()
                )
        except SQLAlchemyError as exc:
            logger.warning("FactStore recency 召回失败 user=%s: %s", user_id, exc)
            raise MemoryStoreError(f"FactStore.recall failed: {exc}") from exc

        now = datetime.utcnow()
        alive = [
            fact
            for fact in (_orm_to_fact(row) for row in rows)
            if self._forgetting.is_alive(fact, now)
        ]
        return alive[:top_k]

    # ------------------------------------------------------------------
    # 删 / 列表 (将来设置页管理 API 复用)
    # ------------------------------------------------------------------
    async def delete(self, scope: MemoryScope, fact_id: str) -> bool:
        """删除一条事实; 归属校验失败 (非本人) 或不存在返回 False."""
        fid = _to_int(fact_id)
        if fid is None:
            return False
        try:
            async with self._factory() as db:
                row = (
                    await db.execute(
                        select(UserFactOrm).where(
                            UserFactOrm.id == fid,
                            UserFactOrm.user_id == _to_int(scope.user_id),
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return False
                await db.delete(row)
                await db.commit()
                return True
        except SQLAlchemyError as exc:
            logger.warning("FactStore.delete 失败 fact=%s: %s", fact_id, exc)
            raise MemoryStoreError(f"FactStore.delete failed: {exc}") from exc

    async def list_by_user(
        self,
        scope: MemoryScope,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Fact], int]:
        """按 user 分页列出全部事实 (updated_at 降序), 返回 (列表, 总数)."""
        uid = _to_int(scope.user_id)
        try:
            async with self._factory() as db:
                stmt = (
                    select(UserFactOrm)
                    .where(UserFactOrm.user_id == uid)
                    .order_by(UserFactOrm.updated_at.desc(), UserFactOrm.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                cnt = select(func.count(UserFactOrm.id)).where(UserFactOrm.user_id == uid)
                rows = (await db.execute(stmt)).scalars().all()
                total = (await db.execute(cnt)).scalar_one()
                return [_orm_to_fact(r) for r in rows], total
        except SQLAlchemyError as exc:
            logger.warning("FactStore.list_by_user 失败 user=%s: %s", scope.user_id, exc)
            raise MemoryStoreError(f"FactStore.list_by_user failed: {exc}") from exc

    # ------------------------------------------------------------------
    # private
    # ------------------------------------------------------------------
    async def _resolve_embedder(self) -> Any | None:
        if self._embedder_resolver is None:
            return None
        try:
            return await self._embedder_resolver()
        except Exception as exc:  # noqa: BLE001
            logger.warning("事实 embedder 解析失败, 降级无向量: %s", exc)
            return None

    async def _embed(self, content: str) -> tuple[list[float], str, int]:
        """算单条内容向量; embedder 不可用/失败返回 ([], "", 0)."""
        embedder = await self._resolve_embedder()
        if embedder is None:
            return [], "", 0
        try:
            raw = await asyncio.to_thread(embedder.embed_documents, [content])
            vec = list(raw[0]) if raw else []
            return vec, _embedder_model(embedder), len(vec)
        except Exception as exc:  # noqa: BLE001
            logger.warning("事实 embedding 计算失败, 降级无向量: %s", exc)
            return [], "", 0


def _embedder_model(embedder: Any) -> str:
    """与 context_mgmt.recall 同口径的 embedder 模型标识."""
    return str(
        getattr(embedder, "_forge_model_id", "")
        or getattr(embedder, "model_name", "")
        or ""
    )


def _as_vector(raw: Any) -> list[float]:
    """把 embed_query 返回值规整成单条向量 list[float]."""
    if raw and isinstance(raw[0], list | tuple):
        return list(raw[0])
    return list(raw or [])


def _orm_to_fact(row: UserFactOrm) -> Fact:
    return Fact(
        id=str(row.id),
        user_id=str(row.user_id),
        content=row.content,
        source=cast(FactSource, row.source),
        source_session_id=(
            str(row.source_session_id) if row.source_session_id is not None else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
