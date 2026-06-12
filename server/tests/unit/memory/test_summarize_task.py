"""memory.summary.service.SummaryService 单测.

直接测 service 的 async 业务实现; Celery task 是它的薄包装, 不单独测.

覆盖:
    1. 无 history -> 不调 LLM, 不 upsert, 返回 None
    2. 仅 system / 空 role 消息 -> 不调 LLM, 不 upsert, 返回 None
    3. 正常 history -> LLM 被调 -> SummaryStore.upsert 被调, 参数正确
    4. LLM 返回空摘要 -> 不 upsert, 返回 None
    5. LLM init 失败 -> 抛 InfrastructureError
    6. SummaryStore.upsert 失败 -> 抛 InfrastructureError
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.memory.summary.service import (
    InfrastructureError,
    get_summary_service,
)


# ---------------------------------------------------------------------------
# 辅助: 全套 patch (db / settings / llm / store)
# ---------------------------------------------------------------------------
def _row(id_: str, role: str, content: str):
    return SimpleNamespace(id=id_, role=role, content=content)


class _Ctx:
    """聚合一组 patch + 暴露各个 mock 给测试用."""

    def __init__(
        self,
        *,
        rows: list,
        llm_summary: str = "好的摘要",
        upsert_raises: Exception | None = None,
        llm_init_raises: Exception | None = None,
        previous=None,
    ) -> None:
        self.rows = rows
        self.llm_summary = llm_summary
        self.upsert_raises = upsert_raises
        self.llm_init_raises = llm_init_raises
        self.previous = previous  # SummaryStore.get 返回的旧摘要 (增量路径)
        self.patches: list = []
        # 暴露给断言用
        self.upsert_mock: AsyncMock | None = None
        self.summarizer_calls: list = []
        self.load_recent_mock: AsyncMock | None = None
        self.load_after_mock: AsyncMock | None = None

    def __enter__(self):
        # 1. settings
        settings = MagicMock()
        settings.memory.summarizer.history_limit = 100
        settings.memory.summarizer.max_summary_tokens = 1500
        settings.memory.summarizer.provider = ""
        settings.memory.summarizer.model = ""
        self.patches.append(patch("forge.config.settings.get_settings", return_value=settings))

        # 2. DB: init_engine + get_session_factory + MessageRepository
        self.patches.append(
            patch(
                "forge.infrastructure.database.database.init_engine",
                MagicMock(),
            )
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
        repo.load_after = AsyncMock(return_value=self.rows)
        self.load_recent_mock = repo.load_recent
        self.load_after_mock = repo.load_after
        self.patches.append(
            patch(
                "forge.infrastructure.database.repositories.chat_message_repo.ChatMessageRepository",
                return_value=repo,
            )
        )

        # 3. LLMGateway: service.py 走 get_llm_gateway(settings) 拿到的 gateway 再传给 Summarizer.
        #    我们 patch get_llm_gateway 返回一个 mock gateway (gateway.complete 不会被本测试调,
        #    因为 Summarizer 也被 patch). llm_init_raises 时让 get_llm_gateway 直接抛.
        if self.llm_init_raises:
            gateway_factory = MagicMock(side_effect=self.llm_init_raises)
        else:
            mock_gateway = MagicMock()
            gateway_factory = MagicMock(return_value=mock_gateway)
        self.patches.append(
            patch("forge.llm.get_llm_gateway", gateway_factory)
        )

        # 4. Summarizer (patch async summarize 方法返回固定值)
        outer = self

        class _FakeSummarizer:
            def __init__(
                self,
                gateway,
                *,
                max_summary_tokens: int = 1500,
                preferred_provider: str | None = None,
                preferred_model: str | None = None,
            ) -> None:
                outer.summarizer_calls.append({
                    "gateway": gateway,
                    "max_tokens": max_summary_tokens,
                    "preferred_provider": preferred_provider,
                    "preferred_model": preferred_model,
                })

            async def summarize(self, messages, *, previous_summary=None):
                outer.summarizer_calls.append(
                    {"messages": messages, "previous_summary": previous_summary}
                )
                return outer.llm_summary

        self.patches.append(
            patch(
                "forge.memory.summary.summarizer.Summarizer",
                _FakeSummarizer,
            )
        )

        # 5. SummaryStore (mock upsert)
        from forge.memory.base import Summary

        upsert = AsyncMock()
        if self.upsert_raises:
            upsert.side_effect = self.upsert_raises
        else:
            # service 用 upsert 返回值, 给它一个最小合法 Summary
            def _make(**kwargs):
                return Summary(
                    session_id=kwargs.get("session_id", "sess_x"),
                    content=kwargs.get("content", ""),
                    covered_until_message_id=kwargs.get("covered_until_message_id"),
                    token_count=kwargs.get("token_count", 0),
                    updated_at=datetime(2026, 5, 17),
                    version=1,
                )

            upsert.side_effect = _make

        class _FakeStore:
            def __init__(self, factory) -> None:
                pass

            async def get(self, session_id):
                return outer.previous

            async def upsert(self, **kwargs):  # noqa: D401
                return await upsert(**kwargs)

        self.upsert_mock = upsert
        self.patches.append(patch("forge.memory.summary.store.SummaryStore", _FakeStore))

        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


# ---------------------------------------------------------------------------
# 测试: 业务路径
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_history_returns_silently() -> None:
    with _Ctx(rows=[]) as ctx:
        await get_summary_service().summarize_session("sess_empty")
    assert ctx.upsert_mock.call_count == 0


@pytest.mark.asyncio
async def test_only_invalid_messages_returns_silently() -> None:
    rows = [
        _row("m1", "system", "ignored"),
        _row("m2", "user", ""),
        _row("m3", "tool", "也忽略"),
    ]
    with _Ctx(rows=rows) as ctx:
        await get_summary_service().summarize_session("sess_x")
    assert ctx.upsert_mock.call_count == 0


@pytest.mark.asyncio
async def test_normal_run_upserts_summary() -> None:
    rows = [
        _row("m1", "user", "你好"),
        _row("m2", "assistant", "你好, 有什么可以帮您"),
        _row("m3", "user", "RAG 怎么做"),
        _row("m4", "assistant", "可以这样这样..."),
    ]
    with _Ctx(rows=rows, llm_summary="摘要正文") as ctx:
        await get_summary_service().summarize_session("sess_ok")

    assert ctx.upsert_mock.call_count == 1
    call = ctx.upsert_mock.call_args.kwargs
    assert call["session_id"] == "sess_ok"
    assert call["content"] == "摘要正文"
    # covered_until_message_id 应该等于 history 最后一条的 id
    assert call["covered_until_message_id"] == "m4"
    assert call["token_count"] >= 1


@pytest.mark.asyncio
async def test_empty_llm_summary_skips_upsert() -> None:
    rows = [_row("m1", "user", "hi"), _row("m2", "assistant", "hello")]
    with _Ctx(rows=rows, llm_summary="") as ctx:
        await get_summary_service().summarize_session("sess_empty_summary")
    assert ctx.upsert_mock.call_count == 0


# ---------------------------------------------------------------------------
# 测试: 异常 -> InfrastructureError
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_llm_init_failure_raises_retryable() -> None:
    rows = [_row("m1", "user", "x"), _row("m2", "assistant", "y")]
    with (
        _Ctx(rows=rows, llm_init_raises=RuntimeError("api key missing")),
        pytest.raises(InfrastructureError, match="Summarizer LLM"),
    ):
        await get_summary_service().summarize_session("sess_x")


@pytest.mark.asyncio
async def test_upsert_failure_raises_retryable() -> None:
    rows = [_row("m1", "user", "x"), _row("m2", "assistant", "y")]
    with (
        _Ctx(rows=rows, upsert_raises=RuntimeError("db down")),
        pytest.raises(InfrastructureError, match="SummaryStore.upsert"),
    ):
        await get_summary_service().summarize_session("sess_x")
