"""Upload size limits and bounded file reads for API routes."""

from __future__ import annotations

from typing import Protocol

from forge.core.exceptions import BadRequest

_MIB = 1024 * 1024

MAX_CHAT_ATTACHMENT_BYTES = 5 * _MIB
MAX_KB_DOCUMENT_BYTES = 200 * _MIB
UPLOAD_READ_CHUNK_BYTES = _MIB


class AsyncReadableUpload(Protocol):
    filename: str | None
    size: int | None

    async def read(self, size: int = -1) -> bytes: ...


def format_upload_limit(size_bytes: int) -> str:
    if size_bytes % _MIB == 0:
        return f"{size_bytes // _MIB}MB"
    return f"{size_bytes} bytes"


async def read_upload_file_limited(
    file: AsyncReadableUpload,
    *,
    max_bytes: int,
    too_large_message: str | None = None,
) -> bytes:
    """Read an uploaded file without accumulating bytes beyond max_bytes."""
    declared_size = getattr(file, "size", None)
    if isinstance(declared_size, int) and declared_size > max_bytes:
        raise BadRequest(
            too_large_message or f"文件过大, 最大支持 {format_upload_limit(max_bytes)}",
            code=40014,
        )

    data = bytearray()
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        if len(data) + len(chunk) > max_bytes:
            raise BadRequest(
                too_large_message or f"文件过大, 最大支持 {format_upload_limit(max_bytes)}",
                code=40014,
            )
        data.extend(chunk)

    if not data:
        raise BadRequest("空文件", code=40013)
    return bytes(data)
