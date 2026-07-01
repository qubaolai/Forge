from __future__ import annotations

from pathlib import Path

import pytest


def test_parent_dict_persists_child_manifest():
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.core.types import Chunk, ChunkType

    parent = Chunk(
        chunk_id="p1",
        chunk_type=ChunkType.PARENT,
        content="parent",
        source_type="text",
        header_path="title",
        doc_id="1001",
        chunk_hash="parent-hash",
    )

    row = KbIngestService._chunk_to_parent_dict(
        parent,
        "2001",
        seq=0,
        child_manifest=[
            {"id": "p1__c_0001", "chunk_hash": "child-2"},
            {"id": "p1__c_0000", "chunk_hash": "child-1"},
        ],
    )

    assert row["extra"]["child_manifest"] == [
        {"id": "p1__c_0001", "chunk_hash": "child-2"},
        {"id": "p1__c_0000", "chunk_hash": "child-1"},
    ]
    assert row["extra"]["child_debug_manifest"] == []


def test_child_debug_manifest_keeps_preview_without_changing_manifest():
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.core.types import Chunk, ChunkMetadata, ChunkType

    child = Chunk(
        chunk_id="p1__c_0000",
        chunk_type=ChunkType.CHILD,
        content="| 字段 | 含义 |\n| --- | --- |\n| amount | 金额 |",
        source_type="table",
        header_path="字段说明",
        parent_id="p1",
        doc_id="1001",
        chunk_hash="child-hash",
        metadata=ChunkMetadata(
            parent_source="table",
            extra={
                "splitter": "table_rows",
                "row_start": 1,
                "row_end": 1,
                "table_index": 0,
            },
        ),
    )

    debug = KbIngestService._child_debug_manifest_by_parent([child])
    manifest = KbIngestService._child_manifest_by_parent([child])

    assert manifest == {"p1": [{"id": "p1__c_0000", "chunk_hash": "child-hash"}]}
    assert debug["p1"][0]["id"] == "p1__c_0000"
    assert debug["p1"][0]["source_type"] == "table"
    assert debug["p1"][0]["splitter"] == "table_rows"
    assert debug["p1"][0]["row_start"] == 1
    assert debug["p1"][0]["row_end"] == 1
    assert debug["p1"][0]["table_index"] == 0
    assert "| amount | 金额 |" in debug["p1"][0]["content_preview"]


@pytest.mark.asyncio
async def test_vector_rebuild_rejects_child_manifest_drift(monkeypatch):
    from forge.api.services.kb_ingest_service import KbIngestError, KbIngestService
    from forge.core.types import Chunk, ChunkType
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm

    class FakeChunkRepo:
        def __init__(self, session) -> None:
            _ = session

        async def list_manifest_by_document(self, document_id):
            _ = document_id
            return [
                {
                    "id": "p1",
                    "chunk_hash": "parent-hash",
                    "child_manifest": [
                        {"id": "p1__c_0000", "chunk_hash": "old-child-hash"}
                    ],
                }
            ]

    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.KbDocumentChunkRepository",
        FakeChunkRepo,
    )

    service = object.__new__(KbIngestService)
    parent = Chunk(
        chunk_id="p1",
        chunk_type=ChunkType.PARENT,
        content="parent",
        source_type="text",
        header_path="title",
        doc_id="1001",
        chunk_hash="parent-hash",
    )
    child = Chunk(
        chunk_id="p1__c_0000",
        chunk_type=ChunkType.CHILD,
        content="child",
        source_type="text",
        header_path="title",
        parent_id="p1",
        doc_id="1001",
        chunk_hash="new-child-hash",
    )
    document = KbDocumentOrm(id=1001, kb_id=2001, name="doc.txt")

    with pytest.raises(KbIngestError, match="父子块 manifest"):
        await service._assert_parent_manifest_unchanged(
            session=object(),
            document=document,
            parents=[parent],
            children=[child],
        )


@pytest.mark.asyncio
async def test_vector_rebuild_does_not_touch_bm25_or_document_status(monkeypatch):
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.core.types import Chunk, ChunkType
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
    from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm

    class Embedder:
        _forge_model_id = "9001"

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["title\n\ncontent"]
            return [[0.1, 0.2]]

    class VectorStore:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.added: list[tuple[list[Chunk], list[list[float]]]] = []

        def delete_by_doc(self, doc_id: str) -> None:
            self.deleted.append(doc_id)

        def add_children(self, children, embeddings) -> None:
            self.added.append((children, embeddings))

    class Runtime:
        def __init__(self) -> None:
            self.store = VectorStore()

        async def resolve_embedding(self):
            return Embedder()

        async def vector_store_for(self, embedder):
            _ = embedder
            return self.store

    class Parser:
        def parse(self, path: Path):
            _ = path
            return [object()]

    class Dispatcher:
        def get(self, path: Path):
            _ = path
            return Parser()

    class Chunker:
        def chunk(self, elements, doc_id, doc_version):
            _ = elements, doc_version
            return [
                Chunk(
                    chunk_id="p1",
                    chunk_type=ChunkType.PARENT,
                    content="content",
                    source_type="text",
                    header_path="title",
                    doc_id=str(doc_id),
                    chunk_hash="hash",
                ),
                Chunk(
                    chunk_id="c1",
                    chunk_type=ChunkType.CHILD,
                    content="content",
                    source_type="text",
                    header_path="title",
                    parent_id="p1",
                    doc_id=str(doc_id),
                )
            ]

    class Bm25MustNotBeCalled:
        def __getattr__(self, name):
            raise AssertionError(f"BM25 不应在向量重建中被调用: {name}")

    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.select_chunker",
        lambda elements, config: Chunker(),
    )
    runtime = Runtime()
    service = KbIngestService(
        bm25_store=Bm25MustNotBeCalled(),
        rag_runtime=runtime,
        parser_dispatcher=Dispatcher(),
    )
    manifest_checked = False

    async def assert_manifest_unchanged(**kwargs) -> None:
        nonlocal manifest_checked
        assert kwargs["session"] is fake_session
        assert kwargs["document"].id == 1001
        assert [p.chunk_id for p in kwargs["parents"]] == ["p1"]
        manifest_checked = True

    monkeypatch.setattr(
        service,
        "_assert_parent_manifest_unchanged",
        assert_manifest_unchanged,
    )
    document = KbDocumentOrm(
        id=1001,
        kb_id=2001,
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=10,
        status="indexed",
        vector_index_status="stale",
    )
    kb = KnowledgeBaseOrm(
        id=2001,
        name="kb",
        visibility="private",
        owner_id=1,
        chunk_size=512,
        chunk_overlap=64,
    )
    fake_session = object()
    guard_called = False

    async def guard() -> None:
        nonlocal guard_called
        guard_called = True

    result = await service.rebuild_vector_index(
        session=fake_session,
        kb=kb,
        document=document,
        file_path=Path("doc.txt"),
        before_vector_write=guard,
    )

    assert manifest_checked is True
    assert guard_called is True
    assert result["status"] == "ready"
    assert document.status == "indexed"
    assert document.embedding_model_id == 9001
    assert document.vector_index_status == "ready"
    assert runtime.store.deleted == ["1001"]
    assert len(runtime.store.added) == 1
