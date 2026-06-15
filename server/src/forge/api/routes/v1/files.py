"""会话文件接口: 附件上传 + 预览 + 下载.

- POST /chat/attachments        : 上传大段输入/文件为会话附件 (source=upload)
- GET  /files/{file_id}/content : 预览 (文本切片)
- GET  /files/{file_id}/download: 单文件下载

鉴权: AuthenticatedUser; 文件按 owner_user_id 归属校验, 越权一律 404 (不泄露存在性)。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response

from forge.api.dependencies import AuthenticatedUser
from forge.api.schemas.file import AttachmentUploadOut, FilePreviewOut
from forge.core.exceptions import BadRequest, NotFound
from forge.core.response import success
from forge.infrastructure.database.database import session_scope
from forge.infrastructure.database.repositories.chat_file_repo import (
    ChatFileRepository,
    ChatFileView,
)
from forge.infrastructure.storage.content_store import slice_text
from forge.infrastructure.storage.workspace_storage import WorkspaceStorage

logger = logging.getLogger(__name__)

router = APIRouter()

# 上传体量上限 (字节). 超大输入应走知识库, 这里只承载会话级附件。
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


@router.post("/chat/attachments")
async def upload_attachment(
    user: AuthenticatedUser,
    session_id: str = Form(..., description="所属会话 id"),
    file: UploadFile = File(..., description="附件文件 (粘贴大段文本可作为 .txt 上传)"),
):
    """上传会话附件 (source=upload), 返回文件元数据。"""
    data = await file.read()
    if not data:
        raise BadRequest("空文件", code=40013)
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise BadRequest("附件过大, 请改用知识库", code=40014)

    filename = file.filename or "attachment.txt"
    try:
        stored = WorkspaceStorage().save(
            user_id=str(user.user_id),
            session_id=str(session_id),
            relpath=filename,
            data=data,
        )
    except ValueError as e:
        raise BadRequest(f"非法文件名: {e}", code=40015) from e

    async with session_scope() as db:
        meta = await ChatFileRepository(db).add(
            owner_user_id=str(user.user_id),
            session_id=str(session_id),
            source="upload",
            filename=filename,
            storage_path=stored.storage_path,
            size_bytes=stored.size_bytes,
            mime_type=file.content_type,
            content_hash=stored.content_hash,
        )

    return success(
        AttachmentUploadOut(
            id=meta.id,
            name=meta.filename,
            size_bytes=meta.size_bytes,
            mime_type=meta.mime_type,
        ).model_dump()
    )


async def _load_owned_file(file_id: str, user_id: str) -> ChatFileView:
    """取文件元数据并校验归属; 越权/不存在抛 NotFound (统一 404, 不泄露存在性)。"""
    async with session_scope() as db:
        meta = await ChatFileRepository(db).get_by_id(file_id)
    if meta is None or meta.owner_user_id != str(user_id):
        raise NotFound("文件不存在", code=40410)
    return meta


@router.get("/files/{file_id}/content")
async def preview_file(
    file_id: str,
    user: AuthenticatedUser,
    start: int = Query(0, ge=0, description="起始行 (1-based, 0=从头)"),
    end: int = Query(0, ge=0, description="结束行 (1-based, 0=到尾)"),
):
    """预览文件文本内容 (可按行区间切片)。"""
    meta = await _load_owned_file(file_id, str(user.user_id))
    try:
        content = WorkspaceStorage().read_text(meta.storage_path)
    except (FileNotFoundError, ValueError, OSError) as e:
        raise NotFound("文件内容不存在", code=40411) from e

    line_range = (start, end) if start and end else None
    sl = slice_text(content, line_range)
    return success(
        FilePreviewOut(
            id=meta.id,
            name=meta.filename,
            text=sl.text,
            total_lines=sl.total_lines,
            returned_range=list(sl.returned_range),
            truncated=sl.truncated,
            mime_type=meta.mime_type,
        ).model_dump()
    )


@router.get("/files/{file_id}/download")
async def download_file(file_id: str, user: AuthenticatedUser):
    """单文件下载。"""
    meta = await _load_owned_file(file_id, str(user.user_id))
    try:
        data = WorkspaceStorage().read(meta.storage_path)
    except (FileNotFoundError, ValueError, OSError) as e:
        raise NotFound("文件内容不存在", code=40411) from e

    download_name = os.path.basename(meta.filename) or "download"
    media_type = meta.mime_type or "application/octet-stream"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name)}",
    }
    return Response(content=data, media_type=media_type, headers=headers)
