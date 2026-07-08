from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_delete_document_cleans_vector_store_by_document_embedding_model(monkeypatch):
    from forge.api.services.kb_ingest_service import KbIngestService
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm

    class VectorStore:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_by_doc(self, doc_id: str) -> int:
            self.deleted.append(doc_id)
            return 1

    class Runtime:
        def __init__(self) -> None:
            self.store = VectorStore()
            self.model_ids: list[str] = []
            self.resolve_called = False

        async def vector_store_for_model_id(self, model_id):
            self.model_ids.append(str(model_id))
            return self.store

        async def resolve_embedding(self):
            self.resolve_called = True
            raise AssertionError("已有 document.embedding_model_id 时不应解析当前绑定模型")

    class Bm25Store:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_by_doc(self, doc_id: str) -> int:
            self.deleted.append(doc_id)
            return 1

    class ChunkRepo:
        def __init__(self, session) -> None:
            _ = session
            self.deleted: list[int] = []

        async def delete_by_document(self, document_id):
            deleted_documents.append(int(document_id))
            return 1

    deleted_documents: list[int] = []
    monkeypatch.setattr(
        "forge.api.services.kb_ingest_service.KbDocumentChunkRepository",
        ChunkRepo,
    )

    runtime = Runtime()
    bm25_store = Bm25Store()
    service = KbIngestService(
        bm25_store=bm25_store,
        rag_runtime=runtime,
        parser_dispatcher=object(),
    )
    document = KbDocumentOrm(
        id=1001,
        kb_id=2001,
        name="doc.txt",
        mime_type="text/plain",
        size_bytes=10,
        embedding_model_id=9001,
    )

    await service.delete_document(session=object(), document=document)

    assert runtime.model_ids == ["9001"]
    assert runtime.resolve_called is False
    assert runtime.store.deleted == ["1001"]
    assert bm25_store.deleted == ["1001"]
    assert deleted_documents == [1001]
