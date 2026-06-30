"""UserUploadStorage 单元测试: 落盘路径 / 同名冲突 / 越界防御 / 读删。"""

from __future__ import annotations

import pytest

from forge.infrastructure.storage.user_upload_storage import UserUploadStorage


@pytest.fixture
def storage(tmp_path):
    return UserUploadStorage(base_dir=tmp_path)


def test_save_path_shape(storage):
    stored = storage.save(user_id="u1", filename="report.txt", data=b"line1\nline2\n")
    # <user_id>/<date>/<filename>
    parts = stored.storage_path.split("/")
    assert parts[0] == "u1"
    assert len(parts[1]) == 10 and parts[1].count("-") == 2  # YYYY-MM-DD
    assert parts[2] == "report.txt"
    assert stored.size_bytes == 12


def test_save_dedup_on_collision(storage):
    a = storage.save(user_id="u1", filename="a.txt", data=b"x")
    b = storage.save(user_id="u1", filename="a.txt", data=b"y")
    assert a.storage_path != b.storage_path
    assert b.storage_path.endswith("a-1.txt")


def test_read_roundtrip(storage):
    stored = storage.save(user_id="u1", filename="n.txt", data="你好\n".encode())
    assert storage.read_text(stored.storage_path) == "你好\n"
    assert storage.read(stored.storage_path) == "你好\n".encode()


def test_filename_only_basename(storage):
    # 含路径分量的文件名只取 basename, 不越界
    stored = storage.save(user_id="u1", filename="../../etc/passwd", data=b"x")
    assert stored.storage_path.endswith("/passwd")
    assert ".." not in stored.storage_path


def test_delete(storage):
    stored = storage.save(user_id="u1", filename="d.txt", data=b"x")
    assert storage.delete(stored.storage_path) is True
    assert storage.delete(stored.storage_path) is False  # 再删返回 False


def test_resolve_rejects_escape(storage):
    with pytest.raises(FileNotFoundError):
        storage.read("u1/2026-06-16/missing.txt")  # 合法但不存在
    # 越界 storage_path 被拒
    assert storage.delete("../escape.txt") is False
