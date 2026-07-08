"""HyDE 查询扩展 (retrieval/query_expansion.py) 单测."""

from __future__ import annotations

import pytest

from forge.retrieval.query_expansion import HydeGenerator


class _FakeResp:
    def __init__(self, content: str | None):
        self.content = content


class _FakeGateway:
    def __init__(self, content: str | None = None, fail: bool = False):
        self._content = content
        self._fail = fail
        self.last_req = None

    async def complete(self, req):
        self.last_req = req
        if self._fail:
            raise RuntimeError("gateway 故障")
        return _FakeResp(self._content)


@pytest.mark.asyncio
async def test_hyde_concats_hypothetical_and_original() -> None:
    gw = _FakeGateway(content="苹果富含维生素C和膳食纤维。")
    hyde = HydeGenerator(gw, concat_original=True)

    out = await hyde.expand("苹果有什么营养")

    assert "苹果富含维生素C" in out
    assert out.endswith("苹果有什么营养")  # 原 query 拼在末尾
    # 走 utility/fast 档 + 缓存
    assert gw.last_req.task_type == "utility"
    assert gw.last_req.model_profile == "fast"
    assert gw.last_req.cache_enabled is True


@pytest.mark.asyncio
async def test_hyde_without_concat_returns_hypothetical_only() -> None:
    gw = _FakeGateway(content="假想答案段落")
    hyde = HydeGenerator(gw, concat_original=False)

    assert await hyde.expand("问题") == "假想答案段落"


@pytest.mark.asyncio
async def test_hyde_degrades_to_query_on_gateway_failure() -> None:
    hyde = HydeGenerator(_FakeGateway(fail=True))

    assert await hyde.expand("原始查询") == "原始查询"


@pytest.mark.asyncio
async def test_hyde_degrades_to_query_on_empty_content() -> None:
    hyde = HydeGenerator(_FakeGateway(content=""))

    assert await hyde.expand("原始查询") == "原始查询"


@pytest.mark.asyncio
async def test_hyde_blank_query_short_circuits_without_llm_call() -> None:
    gw = _FakeGateway(content="不该被用到")
    hyde = HydeGenerator(gw)

    assert await hyde.expand("   ") == ""
    assert gw.last_req is None  # 空 query 不触发 LLM
