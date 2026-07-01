"""ParentChildRetriever._run_recalls 并发与降级行为单测."""

from __future__ import annotations

import pytest

from forge.retrieval.base import RetrievalConfig
from forge.retrieval.pipeline import ParentChildRetriever
from forge.retrieval.recall.base import ChildHit


class _FakeRecall:
    def __init__(self, name: str, hits: list[ChildHit] | None = None, fail: bool = False):
        self._name = name
        self._hits = hits or []
        self._fail = fail
        self.received_query: str | None = None

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, top_k: int, doc_id_filter=None) -> list[ChildHit]:
        self.received_query = query
        if self._fail:
            raise RuntimeError("recall 故障")
        return self._hits


def _hit(chunk_id: str, source: str) -> ChildHit:
    return ChildHit(
        chunk_id=chunk_id,
        parent_id="p1",
        doc_id="d1",
        score=0.9,
        rank=1,
        source=source,
    )


def _retriever(vector: _FakeRecall | None, bm25: _FakeRecall | None) -> ParentChildRetriever:
    retriever = object.__new__(ParentChildRetriever)
    retriever._vector_recall = vector
    retriever._bm25_recall = bm25
    retriever._config = RetrievalConfig()
    return retriever


@pytest.mark.asyncio
async def test_run_recalls_keys_results_by_channel_name() -> None:
    retriever = _retriever(
        _FakeRecall("vector", [_hit("c1", "vector")]),
        _FakeRecall("bm25", [_hit("c2", "bm25")]),
    )

    out = await retriever._run_recalls("q", None)

    assert set(out) == {"vector", "bm25"}
    assert [h.chunk_id for h in out["vector"]] == ["c1"]
    assert [h.chunk_id for h in out["bm25"]] == ["c2"]


@pytest.mark.asyncio
async def test_run_recalls_degrades_failed_channel_without_breaking_other() -> None:
    retriever = _retriever(
        _FakeRecall("vector", [_hit("c1", "vector")]),
        _FakeRecall("bm25", fail=True),
    )

    out = await retriever._run_recalls("q", None)

    assert [h.chunk_id for h in out["vector"]] == ["c1"]
    assert out["bm25"] == []  # 故障路降级为空, 不中断另一路


@pytest.mark.asyncio
async def test_run_recalls_routes_hyde_query_to_vector_only() -> None:
    vector = _FakeRecall("vector", [_hit("c1", "vector")])
    bm25 = _FakeRecall("bm25", [_hit("c2", "bm25")])
    retriever = _retriever(vector, bm25)

    await retriever._run_recalls("原始query", None, vector_query="假想文档\n原始query")

    assert vector.received_query == "假想文档\n原始query"  # 向量用 HyDE 扩展文本
    assert bm25.received_query == "原始query"  # BM25 仍用原始 query
