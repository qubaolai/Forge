from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_search_chunks_reuses_embedding_for_filter_and_retriever(monkeypatch):
    from forge.retrieval.search import search_chunks

    class Embedder:
        _forge_model_id = "model-1"

    class FakeDocRepo:
        def __init__(self, db) -> None:
            _ = db

        async def list_indexed_doc_ids(self, kb_ids):
            assert kb_ids == ["kb1"]
            return ["doc1", "doc2"]

        async def list_vector_ready_doc_ids(self, kb_ids, model_id):
            assert kb_ids == ["kb1"]
            assert model_id == "model-1"
            return ["doc1"]

    class FakeRetriever:
        async def retrieve(
            self,
            *,
            query,
            session,
            doc_id_filter,
            vector_doc_id_filter,
            top_n,
        ):
            assert query == "付款条款"
            assert session == "db"
            assert doc_id_filter == ["doc1", "doc2"]
            assert vector_doc_id_filter == ["doc1"]
            assert top_n == 3
            return ["hit"]

    class FakeRuntime:
        def __init__(self) -> None:
            self.embedder = Embedder()
            self.resolve_calls = 0
            self.build_embedder = None

        async def resolve_embedding(self):
            self.resolve_calls += 1
            return self.embedder

        async def build_retriever(self, *, embedder=None):
            self.build_embedder = embedder
            return FakeRetriever()

    runtime = FakeRuntime()

    monkeypatch.setattr("forge.retrieval.search.KbDocumentRepository", FakeDocRepo)
    monkeypatch.setattr(
        "forge.retrieval.rag_runtime.get_rag_runtime",
        lambda: runtime,
    )

    result = await search_chunks("db", kb_ids=["kb1"], query="付款条款", top_n=3)

    assert result == ["hit"]
    assert runtime.resolve_calls == 1
    assert runtime.build_embedder is runtime.embedder


@pytest.mark.asyncio
async def test_search_chunks_with_trace_calls_trace_retriever(monkeypatch):
    from forge.retrieval.search import search_chunks_with_trace

    class Embedder:
        _forge_model_id = "model-1"

    class FakeDocRepo:
        def __init__(self, db) -> None:
            _ = db

        async def list_indexed_doc_ids(self, kb_ids):
            assert kb_ids == ["kb1"]
            return ["doc1", "doc2"]

        async def list_vector_ready_doc_ids(self, kb_ids, model_id):
            assert kb_ids == ["kb1"]
            assert model_id == "model-1"
            return ["doc1"]

    class FakeRetriever:
        async def retrieve_with_trace(
            self,
            *,
            query,
            session,
            doc_id_filter,
            vector_doc_id_filter,
            top_n,
        ):
            assert query == "付款条款"
            assert session == "db"
            assert doc_id_filter == ["doc1", "doc2"]
            assert vector_doc_id_filter == ["doc1"]
            assert top_n == 3
            return ["hit"], {"recall": {"vector": [], "bm25": []}, "fusion": [], "aggregation": [], "rerank": []}

    class FakeRuntime:
        async def resolve_embedding(self):
            return Embedder()

        async def build_retriever(self, *, embedder=None):
            assert isinstance(embedder, Embedder)
            return FakeRetriever()

    monkeypatch.setattr("forge.retrieval.search.KbDocumentRepository", FakeDocRepo)
    monkeypatch.setattr(
        "forge.retrieval.rag_runtime.get_rag_runtime",
        lambda: FakeRuntime(),
    )

    result, trace = await search_chunks_with_trace(
        "db",
        kb_ids=["kb1"],
        query="付款条款",
        top_n=3,
    )

    assert result == ["hit"]
    assert trace["recall"] == {"vector": [], "bm25": []}
