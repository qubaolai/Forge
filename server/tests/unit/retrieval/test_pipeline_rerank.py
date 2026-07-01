from __future__ import annotations

from forge.retrieval.fusion.base import AggregatedParent
from forge.retrieval.pipeline import ParentChildRetriever
from forge.retrieval.rerankers.base import RerankResult


class StaticReranker:
    model_name = "static"

    def __init__(self, results):
        self.results = results

    def rerank(self, query: str, documents: list[str], top_n: int | None = None):
        _ = query, documents, top_n
        return self.results


def _retriever_with(results) -> ParentChildRetriever:
    retriever = object.__new__(ParentChildRetriever)
    retriever._reranker = StaticReranker(results)
    return retriever


def _candidate(parent_id: str, score: float) -> AggregatedParent:
    return AggregatedParent(
        parent_id=parent_id,
        doc_id="d1",
        fusion_score=score,
        hit_child_count=1,
        hit_chunk_ids=[f"{parent_id}__c_0000"],
    )


def _parent_rows() -> dict[str, dict]:
    return {
        "p1": {
            "chunk_id": "p1",
            "document_id": "d1",
            "kb_id": "kb1",
            "content": "第一段内容",
            "header_path": "",
            "source_type": "text",
            "extra": {},
        },
        "p2": {
            "chunk_id": "p2",
            "document_id": "d1",
            "kb_id": "kb1",
            "content": "第二段内容",
            "header_path": "",
            "source_type": "text",
            "extra": {},
        },
    }


def test_rerank_invalid_index_falls_back_to_fusion_score() -> None:
    retriever = _retriever_with([RerankResult(index=99, score=1.0)])
    candidates = [_candidate("p1", 0.8), _candidate("p2", 0.7)]

    final = retriever._do_rerank("query", candidates, _parent_rows(), top_n=1)

    assert [item.chunk_id for item in final] == ["p1"]
    assert final[0].rerank_score is None
    assert final[0].final_score == 0.8


def test_rerank_empty_results_fall_back_to_fusion_score() -> None:
    retriever = _retriever_with([])
    candidates = [_candidate("p1", 0.8), _candidate("p2", 0.7)]

    final = retriever._do_rerank("query", candidates, _parent_rows(), top_n=2)

    assert [item.chunk_id for item in final] == ["p1", "p2"]
    assert [item.rerank_score for item in final] == [None, None]


def test_rerank_duplicate_index_falls_back_to_fusion_score() -> None:
    retriever = _retriever_with(
        [
            RerankResult(index=0, score=0.9),
            RerankResult(index=0, score=0.8),
        ]
    )
    candidates = [_candidate("p1", 0.8), _candidate("p2", 0.7)]

    final = retriever._do_rerank("query", candidates, _parent_rows(), top_n=2)

    assert [item.chunk_id for item in final] == ["p1", "p2"]
    assert [item.rerank_score for item in final] == [None, None]


def test_rerank_non_finite_score_falls_back_to_fusion_score() -> None:
    retriever = _retriever_with([RerankResult(index=0, score=float("nan"))])
    candidates = [_candidate("p1", 0.8), _candidate("p2", 0.7)]

    final = retriever._do_rerank("query", candidates, _parent_rows(), top_n=1)

    assert [item.chunk_id for item in final] == ["p1"]
    assert final[0].rerank_score is None
