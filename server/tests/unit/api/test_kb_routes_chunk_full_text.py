from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_get_chunk_full_text_returns_parent_content_after_read_auth(monkeypatch):
    from forge.api.routes.v1 import kb as kb_routes

    checked: list[tuple[str, str]] = []

    class ChunkRepo:
        def __init__(self, db) -> None:
            _ = db

        async def get_many_enriched(self, chunk_ids):
            assert chunk_ids == ["doc1__p_0001"]
            return {
                "doc1__p_0001": {
                    "chunk_id": "doc1__p_0001",
                    "document_id": 1001,
                    "document_name": "合同.docx",
                    "kb_id": 2001,
                    "kb_name": "合同库",
                    "content": "完整父块原文",
                    "header_path": "付款条款",
                    "source_type": "table",
                    "extra": {"page_start": 2, "page_end": 3},
                    "source_url": None,
                }
            }

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_readable(self, kb_id, user_id):
            checked.append((kb_id, user_id))
            return object()

    monkeypatch.setattr(kb_routes, "KbDocumentChunkRepository", ChunkRepo)
    monkeypatch.setattr(kb_routes, "KbService", Svc)

    result = await kb_routes.get_chunk_full_text(
        "doc1__p_0001",
        SimpleNamespace(user_id="u1"),
        object(),
    )

    assert checked == [("2001", "u1")]
    assert result["data"]["content"] == "完整父块原文"
    assert result["data"]["document_name"] == "合同.docx"
    assert result["data"]["page_start"] == 2
    assert result["data"]["page_end"] == 3


@pytest.mark.asyncio
async def test_list_document_chunks_checks_read_auth_and_returns_page(monkeypatch):
    from forge.api.routes.v1 import kb as kb_routes
    from forge.api.schemas.knowledge_base import KbDocumentChunkInfo

    calls: list[tuple] = []

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_readable(self, kb_id, user_id):
            calls.append(("auth", kb_id, user_id))
            return object()

        async def get_document(self, kb_id, doc_id):
            calls.append(("doc", kb_id, doc_id))
            return object()

        async def list_document_chunks(self, *, document_id, page, page_size):
            calls.append(("chunks", document_id, page, page_size))
            return [
                KbDocumentChunkInfo(
                    chunk_id="doc1__p_0000",
                    seq=0,
                    header_path="付款条款",
                    source_type="table",
                    page_start=2,
                    page_end=3,
                    token_count=88,
                    content_chars=120,
                    content_preview="父块预览",
                    metadata={"page_start": 2, "page_end": 3},
                    child_count=1,
                    child_debug_manifest=[],
                )
            ], 1

    monkeypatch.setattr(kb_routes, "KbService", Svc)

    result = await kb_routes.list_document_chunks(
        "kb1",
        "doc1",
        SimpleNamespace(user_id="u1"),
        object(),
        page=2,
        page_size=10,
    )

    assert calls == [
        ("auth", "kb1", "u1"),
        ("doc", "kb1", "doc1"),
        ("chunks", "doc1", 2, 10),
    ]
    assert result["data"]["total"] == 1
    assert result["data"]["page"] == 2
    assert result["data"]["items"][0]["chunk_id"] == "doc1__p_0000"
    assert result["data"]["items"][0]["page_start"] == 2


@pytest.mark.asyncio
async def test_search_kb_debug_flag_controls_trace(monkeypatch):
    from forge.api.routes.v1 import kb as kb_routes
    from forge.api.schemas.knowledge_base import KbRetrievalTrace, KbSearchIn

    calls: list[tuple] = []

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_readable(self, kb_id, user_id):
            calls.append(("auth", kb_id, user_id))
            return SimpleNamespace(id=kb_id)

        async def search(self, kb, *, query, top_n, debug):
            calls.append(("search", kb.id, query, top_n, debug))
            trace = KbRetrievalTrace() if debug else None
            return [], trace

    monkeypatch.setattr(kb_routes, "KbService", Svc)

    no_debug = await kb_routes.search_kb(
        "kb1",
        KbSearchIn(query="付款条款", top_n=3, debug=False),
        SimpleNamespace(user_id="u1"),
        object(),
    )
    debug = await kb_routes.search_kb(
        "kb1",
        KbSearchIn(query="付款条款", top_n=3, debug=True),
        SimpleNamespace(user_id="u1"),
        object(),
    )

    assert calls == [
        ("auth", "kb1", "u1"),
        ("search", "kb1", "付款条款", 3, False),
        ("auth", "kb1", "u1"),
        ("search", "kb1", "付款条款", 3, True),
    ]
    assert no_debug["data"]["trace"] is None
    assert debug["data"]["trace"] == {
        "recall": {},
        "fusion": [],
        "aggregation": [],
        "rerank": [],
    }
