from __future__ import annotations

import pytest

from forge.api.upload_limits import (
    MAX_KB_DOCUMENT_BYTES,
    format_upload_limit,
    read_upload_file_limited,
)
from forge.core.exceptions import BadRequest


class FakeUpload:
    filename = "fake.txt"
    size: int | None = None

    def __init__(self, chunks: list[bytes], *, size: int | None = None):
        self._chunks = list(chunks)
        self.size = size

    async def read(self, size: int = -1) -> bytes:
        _ = size
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_kb_document_upload_limit_is_200mb():
    assert MAX_KB_DOCUMENT_BYTES == 200 * 1024 * 1024
    assert format_upload_limit(MAX_KB_DOCUMENT_BYTES) == "200MB"


@pytest.mark.asyncio
async def test_read_upload_file_limited_allows_exact_limit():
    data = await read_upload_file_limited(
        FakeUpload([b"12", b"345"]),
        max_bytes=5,
    )

    assert data == b"12345"


@pytest.mark.asyncio
async def test_read_upload_file_limited_rejects_declared_size_before_read():
    with pytest.raises(BadRequest) as exc:
        await read_upload_file_limited(
            FakeUpload([b"not-read"], size=6),
            max_bytes=5,
        )

    assert exc.value.code == 40014
    assert "最大支持" in exc.value.message


@pytest.mark.asyncio
async def test_read_upload_file_limited_rejects_stream_over_limit():
    with pytest.raises(BadRequest) as exc:
        await read_upload_file_limited(
            FakeUpload([b"123", b"456"]),
            max_bytes=5,
        )

    assert exc.value.code == 40014


@pytest.mark.asyncio
async def test_read_upload_file_limited_rejects_empty_file():
    with pytest.raises(BadRequest) as exc:
        await read_upload_file_limited(FakeUpload([]), max_bytes=5)

    assert exc.value.code == 40013
