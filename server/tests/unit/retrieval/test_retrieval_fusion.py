from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge.config.domains.retrieval import RetrievalConfig as DomainRetrievalConfig
from forge.core.types import Chunk, ChunkType
from forge.retrieval.factory import RetrieverFactory
from forge.retrieval.fusion.rrf import RRFFusion
from forge.retrieval.fusion.weighted import WeightedFusion
from forge.retrieval.recall.base import ChildHit
from forge.retrieval.stores.bm25.sqlite_fts5 import SqliteFTS5BM25Store


def _hit(
    chunk_id: str,
    *,
    parent_id: str = "p1",
    doc_id: str = "d1",
    score: float,
    rank: int,
    source: str = "vector",
) -> ChildHit:
    return ChildHit(
        chunk_id=chunk_id,
        parent_id=parent_id,
        doc_id=doc_id,
        score=score,
        rank=rank,
        source=source,
    )


def test_retriever_factory_maps_weighted_fusion_config() -> None:
    settings = SimpleNamespace(
        retrieval=DomainRetrievalConfig(
            fusion={
                "strategy": "weighted",
                "weighted_vector": 2.0,
                "weighted_bm25": 1.0,
            }
        )
    )

    retriever = RetrieverFactory.create(
        settings=settings,
        child_store=object(),
        bm25_store=object(),
        embedder=object(),
    )

    assert retriever._fusion.name == "weighted"
    assert isinstance(retriever._fusion, WeightedFusion)
    assert retriever._fusion.weights == pytest.approx(
        {
            "vector": 2 / 3,
            "bm25": 1 / 3,
        }
    )


def test_retriever_factory_ignores_disabled_vector_top_k() -> None:
    settings = SimpleNamespace(
        retrieval=DomainRetrievalConfig(
            recall={
                "vector": {"enabled": False, "top_k": 0},
                "bm25": {"enabled": True, "top_k": 10},
            }
        )
    )

    retriever = RetrieverFactory.create(
        settings=settings,
        child_store=None,
        bm25_store=object(),
        embedder=None,
    )

    assert retriever._vector_recall is None
    assert retriever._bm25_recall is not None
    assert retriever._config.vector_enabled is False
    assert retriever._config.bm25_enabled is True


def test_retrieval_config_validates_enabled_bm25_top_k() -> None:
    with pytest.raises(ValueError, match="top_k"):
        DomainRetrievalConfig(
            recall={
                "vector": {"enabled": False, "top_k": 0},
                "bm25": {"enabled": True, "top_k": 0},
            }
        )


def test_retriever_factory_ignores_top_m_when_rerank_inactive() -> None:
    settings = SimpleNamespace(
        retrieval=DomainRetrievalConfig(
            top_n_parent=5,
            top_m_for_rerank=1,
            recall={
                "vector": {"enabled": False, "top_k": 0},
                "bm25": {"enabled": True, "top_k": 10},
            },
            rerank={"enabled": False},
        )
    )

    retriever = RetrieverFactory.create(
        settings=settings,
        child_store=None,
        bm25_store=object(),
        embedder=None,
        reranker=object(),
    )

    assert retriever._config.rerank_enabled is False
    assert retriever._config.top_m_for_rerank == 1


def test_rrf_dedupes_duplicate_hits_from_same_source() -> None:
    fusion = RRFFusion({"rrf_k": 60})

    result = fusion.fuse(
        {
            "vector": [
                _hit("c1", score=0.91, rank=1),
                _hit("c1", score=0.80, rank=5),
            ],
            "bm25": [
                _hit("c1", score=12.0, rank=2, source="bm25"),
            ],
        }
    )

    assert len(result) == 1
    assert result[0].chunk_id == "c1"
    assert result[0].sources == ["vector", "bm25"]
    assert result[0].rank_per_source == {"vector": 1, "bm25": 2}
    assert result[0].fusion_score == pytest.approx((1 / 61) + (1 / 62))


def test_weighted_fusion_dedupes_duplicate_hits_before_normalizing() -> None:
    fusion = WeightedFusion({"weights": {"vector": 1.0}})

    result = fusion.fuse(
        {
            "vector": [
                _hit("c1", score=0.9, rank=2),
                _hit("c1", score=0.1, rank=1),
                _hit("c2", parent_id="p2", score=0.5, rank=3),
            ],
        }
    )

    assert [hit.chunk_id for hit in result] == ["c1", "c2"]
    assert result[0].fusion_score == pytest.approx(1.0)
    assert result[0].rank_per_source == {"vector": 2}
    assert result[1].fusion_score == pytest.approx(0.0)


def test_bm25_store_empty_doc_filter_is_strict_empty(tmp_path) -> None:
    class SpaceTokenizer:
        def tokenize(self, text: str) -> list[str]:
            return [part.lower() for part in text.split() if part.strip()]

        def tokenize_to_string(self, text: str) -> str:
            return " ".join(self.tokenize(text))

    store = SqliteFTS5BM25Store(
        {
            "db_path": str(tmp_path / "bm25.sqlite"),
            "_tokenizer": SpaceTokenizer(),
        }
    )
    try:
        store.add_children(
            [
                Chunk(
                    chunk_id="c1",
                    chunk_type=ChunkType.CHILD,
                    content="alpha beta",
                    source_type="text",
                    header_path="",
                    parent_id="p1",
                    doc_id="d1",
                ),
                Chunk(
                    chunk_id="c2",
                    chunk_type=ChunkType.CHILD,
                    content="alpha gamma",
                    source_type="text",
                    header_path="",
                    parent_id="p2",
                    doc_id="d2",
                ),
            ]
        )

        assert store.search("alpha", top_k=10, doc_id_filter=[]) == []
        assert [hit.doc_id for hit in store.search("alpha", top_k=10, doc_id_filter=["d1"])] == [
            "d1"
        ]
    finally:
        store.close()
