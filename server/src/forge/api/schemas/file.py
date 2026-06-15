"""会话文件相关 schema。"""

from __future__ import annotations

from pydantic import BaseModel


class FileMeta(BaseModel):
    """会话文件元数据 (前端文件卡片 / 历史回看 / SSE file_created)。"""

    id: str
    name: str
    source: str  # upload / generated
    size_bytes: int
    mime_type: str | None = None


class AttachmentUploadOut(BaseModel):
    """附件上传返回。"""

    id: str
    name: str
    size_bytes: int
    mime_type: str | None = None


class FilePreviewOut(BaseModel):
    """文件预览返回 (文本切片)。"""

    id: str
    name: str
    text: str
    total_lines: int
    returned_range: list[int]
    truncated: bool
    mime_type: str | None = None
