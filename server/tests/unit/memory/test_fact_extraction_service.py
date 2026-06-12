"""FactExtractionService 单测.

覆盖 (不依赖真实 DB / LLM):
    1. 合法 JSON -> 逐条写入 + 水位推进
    2. 结构化输出不合规 (StructuredOutputError) -> 不写入, 但照常推水位
    3. 水位后新消息不足 -> 不调 LLM, 不推水位
    4. facts.enabled=False -> 直接返回 0
    5. LLM 调用基础设施错 -> 抛 InfrastructureError, 不推水位
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.llm.gateway import StructuredOutputError
from forge.memory.summary.service import InfrastructureError


def _row(id_: str, role: str, content: str):
    return SimpleNamespace(id=id_, role=role, content=content)


class _Ctx:
    """聚合全套 patch (settings / db / repo / gateway / store / 水位)."""

    def __init__(
        self,
        *,
        rows: list,
        llm_json: str = '{"facts": ["用户偏好 Python"]}',
        llm_raises: Exception | None = None,
        facts_enabled: bool = True,
    ) -> None:
        self.rows = rows
        self.llm_json = llm_json
        self.llm_raises = llm_raises
        self.facts_enabled = facts_enabled
        self.patches: list = []
        # 暴露给断言
        self.write_mock = AsyncMock(return_value=MagicMock())
        self.advance_mock = AsyncMock()
        self.complete_structured: AsyncMock | None = None

    def __enter__(self):
        from forge.memory.facts.service import FactExtractionService

        # 1. settings
        settings = MagicMock()
        settings.memory.facts.enabled = self.facts_enabled
        settings.memory.facts.max_facts_per_turn = 10
        settings.memory.facts.provider = ""
        settings.memory.facts.model = ""
        settings.memory.facts.dedup_threshold = 0.92
        settings.memory.facts.min_score = 0.5
        settings.memory.facts.top_k = 5
        settings.memory.summarizer.history_limit = 100
        self.patches.append(
            patch("forge.config.settings.get_settings", return_value=settings)
        )

        # 2. DB: init_engine + factory + repo
        self.patches.append(
            patch("forge.infrastructure.database.database.init_engine", MagicMock())
        )
        ctx = AsyncMock()
        ctx.__aenter__.return_value = MagicMock()
        ctx.__aexit__.return_value = None
        self.patches.append(
            patch(
                "forge.infrastructure.database.database.get_session_factory",
                return_value=MagicMock(return_value=ctx),
            )
        )
        repo = MagicMock()
        repo.load_after = AsyncMock(return_value=self.rows)
        self.patches.append(
            patch(
                "forge.infrastructure.database.repositories.chat_message_repo.ChatMessageRepository",
                return_value=repo,
            )
        )

        # 3. 水位读写 (patch service 静态方法)
        self.patches.append(
            patch.object(
                FactExtractionService, "_get_watermark", AsyncMock(return_value="m0")
            )
        )
        self.patches.append(
            patch.object(FactExtractionService, "_advance_watermark", self.advance_mock)
        )

        # 4. LLM gateway
        gateway = MagicMock()
        if self.llm_raises:
            gateway.complete_structured = AsyncMock(side_effect=self.llm_raises)
        else:
            gateway.complete_structured = AsyncMock(
                return_value=MagicMock(content=self.llm_json)
            )
        self.complete_structured = gateway.complete_structured
        self.patches.append(
            patch("forge.llm.get_llm_gateway", MagicMock(return_value=gateway))
        )

        # 5. FactStore (patch 装配函数)
        store = MagicMock()
        store.write = self.write_mock
        self.patches.append(
            patch("forge.memory.facts.service.build_fact_store", return_value=store)
        )

        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def _service():
    from forge.memory.facts.service import FactExtractionService

    return FactExtractionService()


ROWS = [
    _row("m1", "user", "我只用 Python, 讨厌 Java"),
    _row("m2", "assistant", "明白, 之后示例都用 Python"),
]


@pytest.mark.asyncio
async def test_valid_json_writes_facts_and_advances_watermark() -> None:
    with _Ctx(rows=ROWS, llm_json='{"facts": ["用户偏好 Python", "用户讨厌 Java"]}') as ctx:
        written = await _service().extract_from_session("s1", "u1")

    assert written == 2
    assert ctx.write_mock.await_count == 2
    call = ctx.write_mock.await_args_list[0]
    assert call.kwargs["content"] == "用户偏好 Python"
    assert call.kwargs["source"] == "llm_extracted"
    assert call.kwargs["source_session_id"] == "s1"
    # 水位推进到最后一条消息
    ctx.advance_mock.assert_awaited_once()
    assert ctx.advance_mock.await_args.args[2] == "m2"


@pytest.mark.asyncio
async def test_structured_output_error_skips_but_advances_watermark() -> None:
    with _Ctx(
        rows=ROWS,
        llm_raises=StructuredOutputError("不合规", last_content="x"),
    ) as ctx:
        written = await _service().extract_from_session("s1", "u1")

    assert written == 0
    assert ctx.write_mock.await_count == 0
    ctx.advance_mock.assert_awaited_once()  # 照常推水位, 不重复烧 LLM


@pytest.mark.asyncio
async def test_insufficient_new_messages_skips_llm_and_watermark() -> None:
    with _Ctx(rows=[_row("m1", "user", "hi")]) as ctx:
        written = await _service().extract_from_session("s1", "u1")

    assert written == 0
    ctx.complete_structured.assert_not_awaited()
    ctx.advance_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_returns_zero_without_any_io() -> None:
    with _Ctx(rows=ROWS, facts_enabled=False) as ctx:
        written = await _service().extract_from_session("s1", "u1")

    assert written == 0
    ctx.complete_structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_llm_infrastructure_error_raises_retryable() -> None:
    with (
        _Ctx(rows=ROWS, llm_raises=RuntimeError("网络挂了")) as ctx,
        pytest.raises(InfrastructureError, match="LLM 调用失败"),
    ):
        await _service().extract_from_session("s1", "u1")
    ctx.advance_mock.assert_not_awaited()  # 失败不推水位, 重试会重做
