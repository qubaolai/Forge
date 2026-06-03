"""语义召回冷路径任务: 为近期消息补算并缓存 embedding.

由 hooks 在 turn.completed 时入队 (与 digest 各自独立)。扫描该 session 近期 user/assistant
消息, 对「无缓存 / source_hash 变更 / 模型切换」的补算 embedding 并 upsert 到 message_embeddings。
下一轮 EmbeddingScorer 即可读缓存向量给早期轮次打分, 避免每轮重算全部历史向量。

幂等: 以 (source_hash, model) 去重。embedder 不可用 / 语义召回关闭 时直接 no-op。
embedder 的 embed_documents 是同步调用, 放 asyncio.to_thread 避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


try:
    from celery import shared_task
except ImportError:  # pragma: no cover

    def shared_task(*args: Any, **kwargs: Any):
        def decorator(fn):
            return fn

        if args and callable(args[0]):
            return args[0]
        return decorator


async def run_embedding_task(session_id: str) -> None:
    """扫描 session 近期消息, 为缺失/过期的补算 embedding 并缓存。"""
    from forge.config.settings import get_settings
    from forge.context_mgmt.recall.embedding_store import MessageEmbeddingStore
    from forge.infrastructure.database.database import get_session_factory, init_engine
    from forge.infrastructure.database.repositories.chat_message_repo import (
        ChatMessageRepository,
    )
    from forge.observability.tracing.tracer import span

    try:
        cfg = get_settings().context.semantic_recall
    except Exception:  # noqa: BLE001
        return
    if not cfg.enabled:
        return

    embedder = _build_embedder()
    if embedder is None:
        return
    model = embedder.model_name
    dim = embedder.dimension

    init_engine()  # 幂等, worker 进程也安全
    factory = get_session_factory()

    async with factory() as db:
        rows = await ChatMessageRepository(db).load_recent(
            session_id, limit=cfg.scan_limit
        )
    if not rows:
        return

    store = MessageEmbeddingStore(factory)

    # 1. 候选 + 幂等去重: (source_hash, model) 都匹配则跳过
    candidates: list[tuple] = []  # (row, content, source_hash)
    for row in rows:
        if row.role not in ("user", "assistant"):
            continue
        content = row.content or ""
        if not content.strip():
            continue
        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        candidates.append((row, content, source_hash))
    if not candidates:
        return

    metas = await store.batch_get_meta([row.id for row, *_ in candidates])
    todo = [c for c in candidates if metas.get(c[0].id) != (c[2], model)]
    if not todo:
        return

    # 2. 批量 embedding (同步调用放线程池)
    contents = [c[1] for c in todo]
    try:
        with span(
            "context.embedding.compute",
            session_id=session_id,
            count=len(contents),
            model=model,
        ):
            vectors = await asyncio.to_thread(embedder.embed_documents, contents)
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedding 计算失败 session=%s: %s", session_id, exc)
        return

    # 3. upsert
    written = 0
    for (row, _content, source_hash), vec in zip(todo, vectors, strict=False):
        try:
            await store.upsert(
                message_id=row.id,
                session_id=row.session_id or session_id,
                model=model,
                dim=dim,
                vector=list(vec),
                source_hash=source_hash,
            )
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding upsert 失败 message=%s: %s", row.id, exc)
    logger.info(
        "embedding 写入 session=%s 新增=%d model=%s", session_id, written, model
    )


def _build_embedder():
    """构造 embedder (复用 RAG 同一进程内单例); 不可用返回 None。"""
    try:
        from forge.config.settings import get_settings
        from forge.retrieval.embedders.factory import build_embedder_from_settings

        return build_embedder_from_settings(get_settings())
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedder 不可用, 跳过 embedding 任务: %s", exc)
        return None


@shared_task(name="context.embedding", bind=True, max_retries=2, default_retry_delay=30)
def embedding_task(self: Any, session_id: str) -> None:
    """生成 + 持久化 session_id 中近期消息的 embedding。"""
    logger.info(
        "embedding 任务开始 session=%s 第 %s 次尝试", session_id, self.request.retries
    )
    asyncio.run(run_embedding_task(session_id))
    logger.info("embedding 任务完成 session=%s", session_id)
