"""语义召回冷路径任务 run_embedding_task 单测 (修订 D).

全套 patch (settings / embedder / db / store), 直接测 async 业务实现;
Celery task 是薄包装, 不单独测。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.context_mgmt.recall.tasks import run_embedding_task


def _row(id_: str, role: str, content: str, session_id: str = "s1"):
    return SimpleNamespace(id=id_, role=role, content=content, session_id=session_id)


class _FakeEmbedder:
    model_name = "fake-emb"
    dimension = 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


class _Ctx:
    """聚合 patch: settings(enabled) / embedder / db factory / repo / store."""

    def __init__(self, *, rows: list, enabled: bool = True, metas: dict | None = None):
        self.rows = rows
        self.enabled = enabled
        self.metas = metas or {}
        self.patches: list = []
        self.upsert_mock = AsyncMock()
        self.prune_mock = AsyncMock()
        self.embedder = _FakeEmbedder()

    def __enter__(self):
        settings = MagicMock()
        settings.context.semantic_recall.enabled = self.enabled
        settings.context.semantic_recall.scan_limit = 50
        settings.context.semantic_recall.retain_turns = 200
        self.patches.append(
            patch("forge.config.settings.get_settings", return_value=settings)
        )

        # embedder: 关闭场景不应被构造, 这里始终给 fake (由 enabled 决定是否调用)
        self.patches.append(
            patch(
                "forge.context_mgmt.recall.tasks._build_embedder",
                return_value=self.embedder if self.enabled else None,
            )
        )

        self.patches.append(
            patch("forge.infrastructure.database.database.init_engine", MagicMock())
        )
        fake_session = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__.return_value = fake_session
        ctx.__aexit__.return_value = None
        fake_factory = MagicMock(return_value=ctx)
        self.patches.append(
            patch(
                "forge.infrastructure.database.database.get_session_factory",
                return_value=fake_factory,
            )
        )
        repo = MagicMock()
        repo.load_recent = AsyncMock(return_value=self.rows)
        self.patches.append(
            patch(
                "forge.infrastructure.database.repositories.chat_message_repo.ChatMessageRepository",
                return_value=repo,
            )
        )

        outer = self

        class _FakeStore:
            def __init__(self, factory) -> None:
                pass

            async def batch_get_meta(self, ids):
                return outer.metas

            async def upsert(self, **kwargs):
                return await outer.upsert_mock(**kwargs)

            async def prune_session(self, session_id, keep):
                return await outer.prune_mock(session_id, keep)

        self.patches.append(
            patch(
                "forge.context_mgmt.recall.embedding_store.MessageEmbeddingStore",
                _FakeStore,
            )
        )

        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


@pytest.mark.asyncio
async def test_disabled_no_op():
    """semantic_recall.enabled=False -> 不算不写."""
    rows = [_row("m1", "user", "hi")]
    with _Ctx(rows=rows, enabled=False) as ctx:
        await run_embedding_task("s1")
    assert ctx.upsert_mock.call_count == 0


@pytest.mark.asyncio
async def test_embeds_and_upserts_candidates():
    """turn 粒度: 只为 user 提问那条算 embedding 并 upsert (assistant/system/空白跳过)."""
    rows = [
        _row("m1", "user", "你好"),
        _row("m2", "assistant", "你好呀"),  # 非 user, 跳过 (只存提问代表整轮)
        _row("m3", "system", "忽略"),       # 非 user, 跳过
        _row("m4", "user", "   "),          # 空白, 跳过
    ]
    with _Ctx(rows=rows) as ctx:
        await run_embedding_task("s1")
    # 仅 m1 (user 提问) 被 upsert
    assert ctx.upsert_mock.call_count == 1
    ids = {c.kwargs["message_id"] for c in ctx.upsert_mock.call_args_list}
    assert ids == {"m1"}
    # 向量/模型/维度写入正确
    first = ctx.upsert_mock.call_args_list[0].kwargs
    assert first["model"] == "fake-emb" and first["dim"] == 2
    assert first["vector"] == [0.1, 0.2]
    # 写入后按 retain_turns 触发淘汰
    ctx.prune_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_idempotent_skips_unchanged():
    """source_hash + model 都匹配的消息跳过 (幂等)."""
    import hashlib

    content = "你好"
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    rows = [_row("m1", "user", content)]
    # 已有缓存且 hash/model 一致 -> 跳过
    with _Ctx(rows=rows, metas={"m1": (h, "fake-emb")}) as ctx:
        await run_embedding_task("s1")
    assert ctx.upsert_mock.call_count == 0
