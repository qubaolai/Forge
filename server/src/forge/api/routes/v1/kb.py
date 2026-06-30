"""/kb 路由: 知识库 CRUD + 文档管理 + 检索测试.

鉴权: AuthenticatedUser。读端点用 get_readable (owner/collaborator/public),
写端点 (建/改/删/上传/重建) 用 get_owned (仅 owner); 越权统一折成 404。

文档入库异步: 上传只落盘 + 建 pending 行 + commit, 再 submit 后台任务推进
状态机, 前端轮询文档状态观察 pending→indexed / failed。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Query, UploadFile

from forge.api.dependencies import AuthenticatedUser, DbSession
from forge.api.schemas.knowledge_base import (
    KbCreateIn,
    KbDocumentListResponse,
    KbDocumentUploadOut,
    KbListResponse,
    KbSearchIn,
    KbSearchResponse,
    KbUpdateIn,
)
from forge.api.services.kb_service import KbService, to_doc_info, to_kb_info
from forge.core.exceptions import BadRequest, Conflict
from forge.core.response import success
from forge.infrastructure.queue import get_task_queue
from forge.infrastructure.storage.file_validation import validate_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb", tags=["kb"])

# KB 文档上限 (字节). 比会话附件宽松, 但仍兜底防超大文件撑爆解析。
_MAX_DOC_BYTES = 50 * 1024 * 1024

# 入库未到终态的状态: 删除会与后台入库任务并发改同一 KB 统计行, 拒绝避免死锁。
_DOC_ACTIVE_STATUSES = {"pending", "parsing", "chunking", "embedding"}


# ----------------------------------------------------------------------
# KB CRUD
# ----------------------------------------------------------------------
@router.post("")
async def create_kb(body: KbCreateIn, user: AuthenticatedUser, db: DbSession):
    kb = await KbService(db).create_kb(owner_id=user.user_id, body=body)
    await db.commit()
    return success(to_kb_info(kb).model_dump(mode="json"))


@router.get("")
async def list_kbs(user: AuthenticatedUser, db: DbSession):
    kbs = await KbService(db).list_kbs(user.user_id)
    items = [to_kb_info(kb).model_dump(mode="json") for kb in kbs]
    return success(KbListResponse(items=items, total=len(items)).model_dump(mode="json"))


@router.get("/{kb_id}")
async def get_kb(kb_id: str, user: AuthenticatedUser, db: DbSession):
    kb = await KbService(db).get_readable(kb_id, user.user_id)
    return success(to_kb_info(kb).model_dump(mode="json"))


@router.put("/{kb_id}")
async def update_kb(kb_id: str, body: KbUpdateIn, user: AuthenticatedUser, db: DbSession):
    svc = KbService(db)
    kb = await svc.get_owned(kb_id, user.user_id)
    kb = await svc.update_kb(kb, body)
    await db.commit()
    return success(to_kb_info(kb).model_dump(mode="json"))


@router.delete("/{kb_id}")
async def delete_kb(kb_id: str, user: AuthenticatedUser, db: DbSession):
    svc = KbService(db)
    kb = await svc.get_owned(kb_id, user.user_id)
    await svc.delete_kb(kb)
    await db.commit()
    return success(None)


# ----------------------------------------------------------------------
# 文档
# ----------------------------------------------------------------------
@router.post("/{kb_id}/documents")
async def upload_document(
    kb_id: str,
    user: AuthenticatedUser,
    db: DbSession,
    file: UploadFile = File(..., description="待入库文档 (pdf/word/markdown/text 等)"),
):
    svc = KbService(db)
    kb = await svc.get_owned(kb_id, user.user_id)

    data = await file.read()
    if not data:
        raise BadRequest("空文件", code=40013)
    if len(data) > _MAX_DOC_BYTES:
        raise BadRequest("文件过大", code=40014)

    filename = file.filename or "document"
    validate_upload(filename, data)  # 类型白名单 + magic-byte 防木马

    doc = await svc.upload_document(
        kb=kb, filename=filename, data=data, mime_type=file.content_type
    )
    await db.commit()
    # commit 后再提交任务, 确保后台任务读得到这一行
    get_task_queue().submit("kb.document.ingest", document_id=str(doc.id))
    return success(
        KbDocumentUploadOut(id=str(doc.id), name=doc.name, status=doc.status).model_dump()
    )


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    user: AuthenticatedUser,
    db: DbSession,
    status: str | None = Query(None, description="按状态过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    svc = KbService(db)
    await svc.get_readable(kb_id, user.user_id)  # 鉴权
    docs, total = await svc.list_documents(
        kb_id, status=status, page=page, page_size=page_size
    )
    items = [to_doc_info(d).model_dump(mode="json") for d in docs]
    return success(
        KbDocumentListResponse(
            items=items, total=total, page=page, page_size=page_size
        ).model_dump(mode="json")
    )


@router.get("/{kb_id}/documents/{doc_id}")
async def get_document(
    kb_id: str, doc_id: str, user: AuthenticatedUser, db: DbSession
):
    svc = KbService(db)
    await svc.get_readable(kb_id, user.user_id)
    doc = await svc.get_document(kb_id, doc_id)
    return success(to_doc_info(doc).model_dump(mode="json"))


@router.delete("/{kb_id}/documents/{doc_id}")
async def delete_document(
    kb_id: str, doc_id: str, user: AuthenticatedUser, db: DbSession
):
    svc = KbService(db)
    kb = await svc.get_owned(kb_id, user.user_id)
    doc = await svc.get_document(kb_id, doc_id)
    # 入库 / 重建进行中禁止删除: 否则与后台任务并发改 KB 统计行触发死锁
    if doc.status in _DOC_ACTIVE_STATUSES or doc.vector_index_status == "rebuilding":
        raise Conflict("文档正在处理中，请待入库/重建完成后再删除", code=40921)
    await svc.delete_document(kb, doc)
    await db.commit()
    return success(None)


@router.post("/{kb_id}/documents/{doc_id}/rebuild")
async def rebuild_document(
    kb_id: str, doc_id: str, user: AuthenticatedUser, db: DbSession
):
    svc = KbService(db)
    await svc.get_owned(kb_id, user.user_id)
    doc = await svc.get_document(kb_id, doc_id)
    if doc.status != "indexed":
        raise BadRequest("文档尚未入库完成, 无法重建向量索引", code=40022)
    get_task_queue().submit("kb.document.rebuild", document_id=str(doc.id))
    return success({"id": str(doc.id), "vector_index_status": "rebuilding"})


# ----------------------------------------------------------------------
# 检索测试
# ----------------------------------------------------------------------
@router.post("/{kb_id}/search")
async def search_kb(
    kb_id: str, body: KbSearchIn, user: AuthenticatedUser, db: DbSession
):
    svc = KbService(db)
    kb = await svc.get_readable(kb_id, user.user_id)
    try:
        hits = await svc.search(kb, query=body.query, top_n=body.top_n)
    except RuntimeError as exc:  # RAG runtime 未装配
        raise BadRequest(f"知识库检索服务未就绪: {exc}", code=40023) from exc
    return success(
        KbSearchResponse(items=hits, total=len(hits), query=body.query).model_dump(
            mode="json"
        )
    )
