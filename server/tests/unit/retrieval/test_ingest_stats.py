from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_reingest_updates_kb_chunk_stats_by_delta(monkeypatch):
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.core.types import Chunk, ChunkType
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
    from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm

    class DocRepo:
        def __init__(self, session) -> None:
            _ = session

        async def update_status(
            self,
            doc_id,
            status,
            *,
            progress=None,
            message=None,
            chunk_count=None,
            mark_indexed=False,
        ) -> None:
            _ = doc_id, status, progress, message, mark_indexed
            if chunk_count is not None:
                document.chunk_count = chunk_count

    stats_delta: list[int] = []

    class KbRepo:
        def __init__(self, session) -> None:
            _ = session

        async def update_stats(self, kb_id, *, chunk_count_delta=0, **kwargs):
            _ = kb_id, kwargs
            stats_delta.append(chunk_count_delta)

    class ChunkRepo:
        def __init__(self, session) -> None:
            _ = session

        async def delete_by_document(self, document_id):
            _ = document_id
            return 2

        async def save_many(self, parents):
            assert [parent["id"] for parent in parents] == ["p1", "p2", "p3"]

    class Runtime:
        async def resolve_embedding(self):
            return None

        async def vector_store_for(self, embedder):
            _ = embedder
            return None

    class Parser:
        def parse(self, path):
            _ = path
            return [object()]

    class Dispatcher:
        def get(self, path):
            _ = path
            return Parser()

    class Chunker:
        def chunk(self, elements, doc_id, doc_version):
            _ = elements, doc_version
            return [
                Chunk(
                    chunk_id="p1",
                    chunk_type=ChunkType.PARENT,
                    content="one",
                    source_type="text",
                    header_path="",
                    doc_id=str(doc_id),
                    chunk_hash="h1",
                ),
                Chunk(
                    chunk_id="p2",
                    chunk_type=ChunkType.PARENT,
                    content="two",
                    source_type="text",
                    header_path="",
                    doc_id=str(doc_id),
                    chunk_hash="h2",
                ),
                Chunk(
                    chunk_id="p3",
                    chunk_type=ChunkType.PARENT,
                    content="three",
                    source_type="text",
                    header_path="",
                    doc_id=str(doc_id),
                    chunk_hash="h3",
                ),
                Chunk(
                    chunk_id="p1__c_0000",
                    chunk_type=ChunkType.CHILD,
                    content="one",
                    source_type="text",
                    header_path="",
                    parent_id="p1",
                    doc_id=str(doc_id),
                    chunk_hash="c1",
                ),
            ]

    class Bm25Store:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.added = 0

        def delete_by_doc(self, doc_id):
            self.deleted.append(doc_id)
            return 1

        def add_children(self, children):
            self.added += len(children)

    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.KbDocumentRepository",
        DocRepo,
    )
    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.KnowledgeBaseRepository",
        KbRepo,
    )
    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.KbDocumentChunkRepository",
        ChunkRepo,
    )
    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.select_chunker",
        lambda elements, config: Chunker(),
    )

    bm25_store = Bm25Store()
    service = KbIngestService(
        bm25_store=bm25_store,
        rag_runtime=Runtime(),
        parser_dispatcher=Dispatcher(),
    )
    document = KbDocumentOrm(
        id=1001,
        kb_id=2001,
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=10,
        status="indexed",
        chunk_count=2,
    )
    kb = KnowledgeBaseOrm(
        id=2001,
        name="kb",
        visibility="private",
        owner_id=1,
        chunk_size=512,
        chunk_overlap=64,
    )

    result = await service.ingest(
        session=object(),
        kb=kb,
        document=document,
        file_path=Path("doc.txt"),
    )

    assert result["parents"] == 3
    assert document.chunk_count == 3
    assert stats_delta == [1]
    assert bm25_store.deleted == ["1001"]
    assert bm25_store.added == 1


@pytest.mark.asyncio
async def test_ingest_parser_does_not_block_event_loop(monkeypatch):
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.core.types import Chunk, ChunkType
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
    from forge.infrastructure.database.orm.knowledge_base_orm import KnowledgeBaseOrm

    class DocRepo:
        def __init__(self, session) -> None:
            _ = session

        async def update_status(self, *args, **kwargs) -> None:
            _ = args, kwargs

    class KbRepo:
        def __init__(self, session) -> None:
            _ = session

        async def update_stats(self, *args, **kwargs) -> None:
            _ = args, kwargs

    class ChunkRepo:
        def __init__(self, session) -> None:
            _ = session

        async def delete_by_document(self, document_id):
            _ = document_id

        async def save_many(self, parents):
            assert [parent["id"] for parent in parents] == ["p1"]

    class Runtime:
        async def resolve_embedding(self):
            return None

        async def vector_store_for(self, embedder):
            _ = embedder
            return None

    started = threading.Event()
    finished = threading.Event()

    class Parser:
        def parse(self, path):
            _ = path
            started.set()
            time.sleep(0.25)
            finished.set()
            return [object()]

    class Dispatcher:
        def get(self, path):
            _ = path
            return Parser()

    class Chunker:
        def chunk(self, elements, doc_id, doc_version):
            _ = elements, doc_version
            return [
                Chunk(
                    chunk_id="p1",
                    chunk_type=ChunkType.PARENT,
                    content="one",
                    source_type="text",
                    header_path="",
                    doc_id=str(doc_id),
                    chunk_hash="h1",
                )
            ]

    class Bm25Store:
        def delete_by_doc(self, doc_id):
            _ = doc_id

        def add_children(self, children):
            _ = children

    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.KbDocumentRepository",
        DocRepo,
    )
    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.KnowledgeBaseRepository",
        KbRepo,
    )
    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.KbDocumentChunkRepository",
        ChunkRepo,
    )
    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.select_chunker",
        lambda elements, config: Chunker(),
    )

    service = KbIngestService(
        bm25_store=Bm25Store(),
        rag_runtime=Runtime(),
        parser_dispatcher=Dispatcher(),
    )
    document = KbDocumentOrm(
        id=1001,
        kb_id=2001,
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=10,
        status="pending",
    )
    kb = KnowledgeBaseOrm(
        id=2001,
        name="kb",
        visibility="private",
        owner_id=1,
        chunk_size=512,
        chunk_overlap=64,
    )

    task = asyncio.create_task(
        service.ingest(
            session=object(),
            kb=kb,
            document=document,
            file_path=Path("doc.txt"),
        )
    )
    start = time.perf_counter()
    await asyncio.sleep(0.05)
    elapsed = time.perf_counter() - start

    assert started.is_set()
    assert elapsed < 0.15
    assert not finished.is_set()

    result = await task
    assert result["parents"] == 1
    assert finished.is_set()
