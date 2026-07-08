"""文件接口: 用户上传 + 预览 + 下载.

- POST /chat/attachments        : 上传文件 (用户上传, 与会话解耦; 发消息时再回填会话)
- GET  /files/{file_id}/content : 预览 (文本切片)
- GET  /files/{file_id}/download: 单文件下载
- GET  /files/message/{id}/archive : 打包某条 assistant 消息生成的文件 (generated)

两类文件分层:
    - 用户上传 (user_files + UserUploadStorage): 与会话解耦, 上传时不绑 session。
    - LLM 生成 (chat_files + WorkspaceStorage): 随会话, write_file 产出。
预览/下载按 file_id 统一解析: 先查 user_files, 未命中再查 chat_files。

鉴权: AuthenticatedUser; 文件按 owner_user_id 归属校验, 越权一律 404 (不泄露存在性)。
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import Response

from forge.api.dependencies import AuthenticatedUser
from forge.api.schemas.file import AttachmentUploadOut, FilePreviewOut
from forge.api.upload_limits import (
    MAX_CHAT_ATTACHMENT_BYTES,
    format_upload_limit,
    read_upload_file_limited,
)
from forge.core.exceptions import BadRequest, NotFound
from forge.core.response import success
from forge.infrastructure.database.database import session_scope
from forge.infrastructure.database.repositories.chat_file_repo import ChatFileRepository
from forge.infrastructure.database.repositories.user_file_repo import UserFileRepository
from forge.infrastructure.storage.content_store import slice_text
from forge.infrastructure.storage.file_validation import validate_upload
from forge.infrastructure.storage.user_upload_storage import UserUploadStorage
from forge.infrastructure.storage.workspace_storage import WorkspaceStorage

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chat/attachments")
async def upload_attachment(
    user: AuthenticatedUser,
    file: UploadFile = File(..., description="附件文件 (粘贴大段文本可作为 .txt 上传)"),
):
    """上传用户文件 (与会话解耦), 返回文件元数据。

    上传时不绑会话; 发消息时由 TurnPreparer 回填 session_id/message_id。
    长期未关联会话的孤儿文件由后台定期清理。
    """
    data = await read_upload_file_limited(
        file,
        max_bytes=MAX_CHAT_ATTACHMENT_BYTES,
        too_large_message=(
            f"附件过大, 最大支持 {format_upload_limit(MAX_CHAT_ATTACHMENT_BYTES)}, "
            "请改用知识库"
        ),
    )

    filename = file.filename or "attachment.txt"
    # 类型校验 (白/黑名单 + magic-byte 防木马); 不通过抛 BadRequest。
    validate_upload(filename, data)

    try:
        stored = UserUploadStorage().save(
            user_id=str(user.user_id),
            filename=filename,
            data=data,
        )
    except ValueError as e:
        raise BadRequest(f"非法文件名: {e}", code=40015) from e

    async with session_scope() as db:
        meta = await UserFileRepository(db).add(
            owner_user_id=str(user.user_id),
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


class _Storage(Protocol):
    def read(self, storage_path: str) -> bytes: ...
    def read_text(self, storage_path: str, encoding: str = ...) -> str: ...


@dataclass(frozen=True)
class _ResolvedFile:
    """统一解析后的文件 (无论来自 user_files 还是 chat_files)。"""

    id: str
    filename: str
    mime_type: str | None
    storage_path: str
    storage: _Storage

    def read(self) -> bytes:
        return self.storage.read(self.storage_path)

    def read_text(self) -> str:
        return self.storage.read_text(self.storage_path)


async def _resolve_owned_file(file_id: str, user_id: str) -> _ResolvedFile:
    """按 file_id 统一解析归属本用户的文件: 先 user_files, 再 chat_files。

    读文件不限来源 (上传 / 生成均可); 越权/不存在统一 404 (不泄露存在性)。
    """
    uid = str(user_id)
    async with session_scope() as db:
        uf = await UserFileRepository(db).get_by_id(file_id)
        if uf is not None and uf.owner_user_id == uid:
            return _ResolvedFile(
                uf.id, uf.filename, uf.mime_type, uf.storage_path, UserUploadStorage()
            )
        cf = await ChatFileRepository(db).get_by_id(file_id)
        if cf is not None and cf.owner_user_id == uid:
            return _ResolvedFile(
                cf.id, cf.filename, cf.mime_type, cf.storage_path, WorkspaceStorage()
            )
    raise NotFound("文件不存在", code=40410)


@router.get("/files/{file_id}/content")
async def preview_file(
    file_id: str,
    user: AuthenticatedUser,
    start: int = Query(0, ge=0, description="起始行 (1-based, 0=从头)"),
    end: int = Query(0, ge=0, description="结束行 (1-based, 0=到尾)"),
):
    """预览文件文本内容 (可按行区间切片)。"""
    meta = await _resolve_owned_file(file_id, str(user.user_id))
    try:
        content = meta.read_text()
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
    meta = await _resolve_owned_file(file_id, str(user.user_id))
    try:
        data = meta.read()
    except (FileNotFoundError, ValueError, OSError) as e:
        raise NotFound("文件内容不存在", code=40411) from e

    download_name = os.path.basename(meta.filename) or "download"
    media_type = meta.mime_type or "application/octet-stream"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name)}",
    }
    return Response(content=data, media_type=media_type, headers=headers)


@router.get("/files/message/{message_id}/archive")
async def download_message_archive(message_id: str, user: AuthenticatedUser):
    """打包下载某条 assistant 消息生成的全部文件 (zip, 仅 generated)。"""
    async with session_scope() as db:
        files = await ChatFileRepository(db).list_by_message(message_id)
    owned = [
        f for f in files
        if f.owner_user_id == str(user.user_id) and f.source == "generated"
    ]
    if not owned:
        raise NotFound("无可打包的生成文件", code=40412)

    ws = WorkspaceStorage()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in owned:
            try:
                zf.writestr(f.filename, ws.read(f.storage_path))
            except (FileNotFoundError, ValueError, OSError):
                continue  # 物理文件缺失则跳过, 不阻断整包

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote('files-' + message_id + '.zip')}",
    }
    return Response(content=buf.getvalue(), media_type="application/zip", headers=headers)
