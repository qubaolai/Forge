from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge.core.exceptions import BadRequest, Conflict


@pytest.mark.asyncio
async def test_upload_document_marks_failed_when_queue_submit_fails(monkeypatch):
    from forge.api.routes.v1 import kb as kb_routes

    doc = SimpleNamespace(
        id=1001,
        name="doc.txt",
        status="pending",
        status_message=None,
        progress=0,
    )
    commits = 0

    class Upload:
        filename = "doc.txt"
        content_type = "text/plain"

        async def read(self):
            return b"hello"

    class Db:
        async def commit(self):
            nonlocal commits
            commits += 1

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_owned(self, kb_id, user_id):
            assert kb_id == "kb1"
            assert user_id == "u1"
            return SimpleNamespace(id=1)

        async def upload_document(self, *, kb, filename, data, mime_type):
            _ = kb, filename, data, mime_type
            return doc

    class Queue:
        def submit(self, name, **kwargs):
            _ = name, kwargs
            raise RuntimeError("broker down")

    monkeypatch.setattr(kb_routes, "KbService", Svc)
    monkeypatch.setattr(kb_routes, "validate_upload", lambda filename, data: None)
    monkeypatch.setattr(kb_routes, "get_task_queue", lambda: Queue())

    with pytest.raises(BadRequest, match="提交入库任务失败"):
        await kb_routes.upload_document(
            "kb1",
            SimpleNamespace(user_id="u1"),
            Db(),
            Upload(),
        )

    assert doc.status == "failed"
    assert doc.status_message == "提交入库任务失败: broker down"
    assert doc.progress == 0
    assert commits == 2


@pytest.mark.asyncio
async def test_rebuild_document_marks_rebuilding_before_queue_submit(monkeypatch):
    from forge.api.routes.v1 import kb as kb_routes

    doc = SimpleNamespace(
        id=1001,
        status="indexed",
        vector_index_status="ready",
        vector_index_error="previous",
    )
    commits = 0
    submitted: list[tuple[str, str]] = []

    class Db:
        async def commit(self):
            nonlocal commits
            commits += 1

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_owned(self, kb_id, user_id):
            assert kb_id == "kb1"
            assert user_id == "u1"
            return object()

        async def get_document(self, kb_id, doc_id):
            assert kb_id == "kb1"
            assert doc_id == "1001"
            return doc

    class Queue:
        def submit(self, name, **kwargs):
            submitted.append((name, kwargs["document_id"]))

    monkeypatch.setattr(kb_routes, "KbService", Svc)
    monkeypatch.setattr(kb_routes, "get_task_queue", lambda: Queue())

    result = await kb_routes.rebuild_document(
        "kb1",
        "1001",
        SimpleNamespace(user_id="u1"),
        Db(),
    )

    assert result["data"] == {"id": "1001", "vector_index_status": "rebuilding"}
    assert doc.vector_index_status == "rebuilding"
    assert doc.vector_index_error is None
    assert commits == 1
    assert submitted == [("kb.document.rebuild", "1001")]


@pytest.mark.asyncio
async def test_rebuild_document_restores_vector_status_when_queue_submit_fails(
    monkeypatch,
):
    from forge.api.routes.v1 import kb as kb_routes

    doc = SimpleNamespace(
        id=1001,
        status="indexed",
        vector_index_status="ready",
        vector_index_error="previous",
    )
    commits = 0

    class Db:
        async def commit(self):
            nonlocal commits
            commits += 1

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_owned(self, kb_id, user_id):
            _ = kb_id, user_id
            return object()

        async def get_document(self, kb_id, doc_id):
            _ = kb_id, doc_id
            return doc

    class Queue:
        def submit(self, name, **kwargs):
            _ = name, kwargs
            raise RuntimeError("broker down")

    monkeypatch.setattr(kb_routes, "KbService", Svc)
    monkeypatch.setattr(kb_routes, "get_task_queue", lambda: Queue())

    with pytest.raises(BadRequest, match="提交向量重建任务失败"):
        await kb_routes.rebuild_document(
            "kb1",
            "1001",
            SimpleNamespace(user_id="u1"),
            Db(),
        )

    assert doc.vector_index_status == "ready"
    assert doc.vector_index_error == "previous"
    assert commits == 2


@pytest.mark.asyncio
async def test_rebuild_document_rejects_duplicate_rebuild(monkeypatch):
    from forge.api.routes.v1 import kb as kb_routes

    doc = SimpleNamespace(
        id=1001,
        status="indexed",
        vector_index_status="rebuilding",
        vector_index_error=None,
    )

    class Db:
        async def commit(self):
            raise AssertionError("重复重建不应提交事务")

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_owned(self, kb_id, user_id):
            _ = kb_id, user_id
            return object()

        async def get_document(self, kb_id, doc_id):
            _ = kb_id, doc_id
            return doc

    monkeypatch.setattr(kb_routes, "KbService", Svc)

    with pytest.raises(Conflict, match="正在重建"):
        await kb_routes.rebuild_document(
            "kb1",
            "1001",
            SimpleNamespace(user_id="u1"),
            Db(),
        )


@pytest.mark.asyncio
async def test_reingest_document_marks_pending_before_queue_submit(monkeypatch):
    from forge.api.routes.v1 import kb as kb_routes

    doc = SimpleNamespace(
        id=1001,
        status="indexed",
        status_message="previous",
        progress=100,
        vector_index_status="ready",
        vector_index_error="previous vector",
        storage_path="kb1/1001/doc.txt",
    )
    commits = 0
    submitted: list[tuple[str, str]] = []

    class Db:
        async def commit(self):
            nonlocal commits
            commits += 1

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_owned(self, kb_id, user_id):
            _ = kb_id, user_id
            return object()

        async def get_document(self, kb_id, doc_id):
            _ = kb_id, doc_id
            return doc

    class Queue:
        def submit(self, name, **kwargs):
            submitted.append((name, kwargs["document_id"]))

    monkeypatch.setattr(kb_routes, "KbService", Svc)
    monkeypatch.setattr(kb_routes, "get_task_queue", lambda: Queue())

    result = await kb_routes.reingest_document(
        "kb1",
        "1001",
        SimpleNamespace(user_id="u1"),
        Db(),
    )

    assert result["data"] == {"id": "1001", "status": "pending"}
    assert doc.status == "pending"
    assert doc.status_message is None
    assert doc.progress == 0
    assert doc.vector_index_status == "stale"
    assert doc.vector_index_error is None
    assert commits == 1
    assert submitted == [("kb.document.ingest", "1001")]


@pytest.mark.asyncio
async def test_reingest_document_restores_status_when_queue_submit_fails(monkeypatch):
    from forge.api.routes.v1 import kb as kb_routes

    doc = SimpleNamespace(
        id=1001,
        status="indexed",
        status_message="previous",
        progress=100,
        vector_index_status="ready",
        vector_index_error="previous vector",
        storage_path="kb1/1001/doc.txt",
    )
    commits = 0

    class Db:
        async def commit(self):
            nonlocal commits
            commits += 1

    class Svc:
        def __init__(self, db) -> None:
            _ = db

        async def get_owned(self, kb_id, user_id):
            _ = kb_id, user_id
            return object()

        async def get_document(self, kb_id, doc_id):
            _ = kb_id, doc_id
            return doc

    class Queue:
        def submit(self, name, **kwargs):
            _ = name, kwargs
            raise RuntimeError("broker down")

    monkeypatch.setattr(kb_routes, "KbService", Svc)
    monkeypatch.setattr(kb_routes, "get_task_queue", lambda: Queue())

    with pytest.raises(BadRequest, match="提交重新入库任务失败"):
        await kb_routes.reingest_document(
            "kb1",
            "1001",
            SimpleNamespace(user_id="u1"),
            Db(),
        )

    assert doc.status == "indexed"
    assert doc.status_message == "previous"
    assert doc.progress == 100
    assert doc.vector_index_status == "ready"
    assert doc.vector_index_error == "previous vector"
    assert commits == 2
