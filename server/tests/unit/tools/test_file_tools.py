"""read_file / write_file 单测."""

from __future__ import annotations

from pathlib import Path

from forge.tools.builtin.file.read_file import ReadFile
from forge.tools.builtin.file.write_file import WriteFile


def test_read_file_returns_text(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")
    out = ReadFile().run({"path": str(target)})
    assert out["ok"] is True
    assert out["content"] == "hello\nworld\n"
    assert out["total_lines"] == 2
    assert out["binary"] is False


def test_read_file_with_offset_limit(tmp_path: Path) -> None:
    target = tmp_path / "many.txt"
    target.write_text("\n".join(f"line-{i}" for i in range(1, 11)) + "\n", encoding="utf-8")
    out = ReadFile().run({"path": str(target), "offset": 3, "limit": 4})
    assert out["ok"] is True
    assert out["first_line"] == 3
    assert out["last_line"] == 6
    assert out["content"] == "line-3\nline-4\nline-5\nline-6\n"


def test_read_file_truncates_large_output(tmp_path: Path) -> None:
    target = tmp_path / "big.txt"
    big = "x" * 5000
    target.write_text(big, encoding="utf-8")
    out = ReadFile().run({"path": str(target), "max_chars": 1000})
    assert out["truncated"] is True
    assert len(out["content"]) == 1000


def test_read_file_binary(tmp_path: Path) -> None:
    target = tmp_path / "img.bin"
    target.write_bytes(bytes(range(256)))
    out = ReadFile().run({"path": str(target)})
    assert out["ok"] is True
    assert out["binary"] is True
    assert "hex_preview" in out


def test_read_file_missing(tmp_path: Path) -> None:
    out = ReadFile().run({"path": str(tmp_path / "nope.txt")})
    assert out["ok"] is False
    assert "不存在" in out["error"]


def test_write_file_creates_and_writes(tmp_path: Path) -> None:
    target = tmp_path / "sub/dir/out.txt"
    out = WriteFile().run({"path": str(target), "content": "你好世界"})
    assert out["ok"] is True
    assert out["existed"] is False
    assert target.read_text(encoding="utf-8") == "你好世界"
    assert out["bytes_written"] == len("你好世界".encode())


def test_write_file_append(tmp_path: Path) -> None:
    target = tmp_path / "log.txt"
    target.write_text("line1\n", encoding="utf-8")
    out = WriteFile().run({"path": str(target), "content": "line2\n", "mode": "append"})
    assert out["ok"] is True
    assert out["existed"] is True
    assert target.read_text(encoding="utf-8") == "line1\nline2\n"


def test_write_file_size_limit(tmp_path: Path) -> None:
    target = tmp_path / "huge.txt"
    out = WriteFile().run({"path": str(target), "content": "x" * 100, "max_bytes": 10})
    assert out["ok"] is False
    assert "max_bytes" in out["error"]
    assert not target.exists()


def test_write_file_invalid_mode(tmp_path: Path) -> None:
    out = WriteFile().run({"path": str(tmp_path / "x.txt"), "content": "x", "mode": "weird"})
    assert out["ok"] is False
    assert "mode" in out["error"]
