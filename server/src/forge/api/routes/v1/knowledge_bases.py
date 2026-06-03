"""/kb 路由 - 知识库 + 文档 CRUD + 上传.

设计:
    - 同步上传 (UploadFile → 读 bytes → ingest 同步跑完). 大文件 / 异步化留 V2.
    - 鉴权: 所有读走 user 可访问性 (owner/public), 所有写仅 owner 可执行.
    - 服务实例从 app.state 注入 (lifespan 装配).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Query, Request, UploadFile

from forge.api.dependencies import AuthenticatedUser, DbSession
from forge.api.schemas.knowledge_base import (
    KbCreateIn,
    KbDocumentInfo,
    KbDocumentListResponse,
    KbInfo,
    KbListResponse,
)
from forge.api.services.kb_service import KbService
from forge.core.response import success

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["knowledge_base"])


def _svc(request: Request) -> KbService:
    """从 app.state 取出 KbService. lifespan 启动失败时报清晰错误."""
    svc = getattr(request.app.state, "kb_service", None)
    if svc is None:
        raise RuntimeError(
            "app.state.kb_service 未初始化, 检查 lifespan 是否加载 RAG 栈 "
            "(extras: poetry install -E rag)"
        )
    return svc


def _kb_to_info(kb) -> KbInfo:
    return KbInfo(
        id=str(kb.id),
        name=kb.name,
        description=kb.description,
        visibility=kb.visibility,
        document_count=kb.document_count,
        chunk_count=kb.chunk_count,
        size_bytes=kb.size_bytes,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


def _doc_to_info(doc) -> KbDocumentInfo:
    return KbDocumentInfo(
        id=str(doc.id),
        kb_id=str(doc.kb_id),
        name=doc.name,
        source=doc.source,
        source_url=doc.source_url,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        content_hash=doc.content_hash,
        status=doc.status,
        status_message=doc.status_message,
        progress=doc.progress,
        chunk_count=doc.chunk_count,
        indexed_at=doc.indexed_at,
        embedding_model_id=str(doc.embedding_model_id) if doc.embedding_model_id else None,
        vector_index_status=doc.vector_index_status,
        vector_index_error=doc.vector_index_error,
        vector_indexed_at=doc.vector_indexed_at,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


# ----------------------------------------------------------------------
# KB CRUD
# ----------------------------------------------------------------------
@router.post("")
async def create_kb(
    body: KbCreateIn,
    request: Request,
    user: AuthenticatedUser,
    db: DbSession,
):
    svc = _svc(request)
    kb = await svc.create_kb(
        db,
        user_id=user.user_id,
        name=body.name,
        description=body.description,
        visibility=body.visibility,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
    )
    await db.commit()
    return success(_kb_to_info(kb).model_dump(mode="json"))


@router.get("")
async def list_kbs(request: Request, user: AuthenticatedUser, db: DbSession):
    svc = _svc(request)
    kbs = await svc.list_kbs_for_user(db, user_id=user.user_id)
    return success(
        KbListResponse(
            items=[_kb_to_info(kb) for kb in kbs],
            total=len(kbs),
        ).model_dump(mode="json")
    )


@router.get("/{kb_id}")
async def get_kb(kb_id: str, request: Request, user: AuthenticatedUser, db: DbSession):
    svc = _svc(request)
    kb = await svc.get_kb(db, kb_id=kb_id, user_id=user.user_id)
    return success(_kb_to_info(kb).model_dump(mode="json"))


@router.delete("/{kb_id}")
async def delete_kb(kb_id: str, request: Request, user: AuthenticatedUser, db: DbSession):
    svc = _svc(request)
    await svc.delete_kb(db, kb_id=kb_id, user_id=user.user_id)
    await db.commit()
    return success({"kb_id": kb_id, "deleted": True})


# ----------------------------------------------------------------------
# KB 文档 CRUD + 上传
# ----------------------------------------------------------------------
@router.post("/{kb_id}/documents")
async def upload_document(
    kb_id: str,
    request: Request,
    user: AuthenticatedUser,
    db: DbSession,
    file: UploadFile = File(...),
):
    """同步上传 + 完整入库. 接口返回时文档状态已是 indexed (失败时是 failed)."""
    svc = _svc(request)
    content = await file.read()
    try:
        doc = await svc.upload_document(
            db,
            kb_id=kb_id,
            user_id=user.user_id,
            filename=file.filename or "unnamed",
            mime_type=file.content_type or "application/octet-stream",
            content=content,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return success(_doc_to_info(doc).model_dump(mode="json"))


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    request: Request,
    user: AuthenticatedUser,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = None,
):
    svc = _svc(request)
    docs, total = await svc.list_documents(
        db,
        kb_id=kb_id,
        user_id=user.user_id,
        page=page,
        page_size=page_size,
        status=status,
    )
    return success(
        KbDocumentListResponse(
            items=[_doc_to_info(d) for d in docs],
            total=total,
            page=page,
            page_size=page_size,
        ).model_dump(mode="json")
    )


@router.get("/{kb_id}/documents/{document_id}")
async def get_document(
    kb_id: str,
    document_id: str,
    request: Request,
    user: AuthenticatedUser,
    db: DbSession,
):
    svc = _svc(request)
    doc = await svc.get_document(db, kb_id=kb_id, document_id=document_id, user_id=user.user_id)
    return success(_doc_to_info(doc).model_dump(mode="json"))


@router.delete("/{kb_id}/documents/{document_id}")
async def delete_document(
    kb_id: str,
    document_id: str,
    request: Request,
    user: AuthenticatedUser,
    db: DbSession,
):
    svc = _svc(request)
    await svc.delete_document(db, kb_id=kb_id, document_id=document_id, user_id=user.user_id)
    await db.commit()
    return success({"document_id": document_id, "deleted": True})
