"""WorkspaceStorage 单元测试: 沙盒读写 + 用户/会话隔离 + 路径越界防御 (安全关键).

沙盒越界防御是 write_file 工具放开「可写」后的唯一防线, 这里重点覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.infrastructure.storage.workspace_storage import WorkspaceStorage


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """所有沙盒落到 tmp_path, 避免污染真实用户目录。"""
    home = tmp_path / "assistant_home"
    monkeypatch.setenv("FORGE_HOME", str(home))
    return home


def test_save_and_read_roundtrip():
    ws = WorkspaceStorage()
    stored = ws.save(user_id="u1", session_id="s1", relpath="app.py", data=b"print('hi')")
    assert stored.storage_path == "u1/s1/app.py"
    assert stored.size_bytes == len(b"print('hi')")
    assert ws.read_text(stored.storage_path) == "print('hi')"


def test_save_supports_subdirectories():
    ws = WorkspaceStorage()
    stored = ws.save(user_id="u1", session_id="s1", relpath="src/main.py", data=b"x=1")
    assert stored.storage_path == "u1/s1/src/main.py"
    assert ws.read_text(stored.storage_path) == "x=1"


def test_overwrite_same_path():
    ws = WorkspaceStorage()
    ws.save(user_id="u1", session_id="s1", relpath="a.txt", data=b"v1")
    s2 = ws.save(user_id="u1", session_id="s1", relpath="a.txt", data=b"v2")
    assert ws.read_text(s2.storage_path) == "v2"


@pytest.mark.parametrize("bad", ["/etc/passwd", "../escape.py", "a/../../b.py", "../../x"])
def test_reject_path_traversal(bad):
    ws = WorkspaceStorage()
    with pytest.raises(ValueError):
        ws.save(user_id="u1", session_id="s1", relpath=bad, data=b"x")


def test_reject_bad_session_id():
    ws = WorkspaceStorage()
    with pytest.raises(ValueError):
        ws.save(user_id="u1", session_id="../s", relpath="a.py", data=b"x")


def test_user_session_isolation():
    ws = WorkspaceStorage()
    a = ws.save(user_id="u1", session_id="s1", relpath="f.py", data=b"A")
    b = ws.save(user_id="u2", session_id="s1", relpath="f.py", data=b"B")
    assert a.storage_path != b.storage_path
    assert ws.read_text(a.storage_path) == "A"
    assert ws.read_text(b.storage_path) == "B"


def test_delete_session_removes_dir():
    ws = WorkspaceStorage()
    s = ws.save(user_id="u1", session_id="s1", relpath="f.py", data=b"A")
    ws.save(user_id="u1", session_id="s1", relpath="sub/g.py", data=b"B")
    assert ws.delete_session("u1", "s1") is True
    with pytest.raises((FileNotFoundError, OSError)):
        ws.read(s.storage_path)
    # 二次删除不存在 → False
    assert ws.delete_session("u1", "s1") is False


def test_read_rejects_traversal_storage_path():
    ws = WorkspaceStorage()
    with pytest.raises(ValueError):
        ws.read("../outside.py")
