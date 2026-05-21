"""grep 工具单测 (覆盖 ripgrep + Python 降级)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.tools.builtin.code.grep import Grep, _run_python


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("foo bar\nhello world\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("HELLO world\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("def hello():\n    return foo\n", encoding="utf-8")
    (tmp_path / "doc.md").write_text("hello in markdown\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("hello in git\n", encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_grep_basic(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    out = await Grep().arun({"pattern": "hello", "path": str(tmp_path)})
    assert out["ok"] is True
    files = {m["file"].rsplit("/", 1)[-1] for m in out["matches"]}
    assert "a.py" in files
    assert "sub/c.py" in {m["file"].split(str(tmp_path) + "/")[-1] for m in out["matches"]}
    # .git/config 不应被搜
    assert "config" not in files


@pytest.mark.asyncio
async def test_grep_case_insensitive(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    out = await Grep().arun({"pattern": "hello", "path": str(tmp_path), "case_insensitive": True})
    assert out["ok"] is True
    files = [m["file"].split(str(tmp_path) + "/")[-1] for m in out["matches"]]
    assert any("b.py" in f for f in files)  # HELLO 也被命中


@pytest.mark.asyncio
async def test_grep_include_glob(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    out = await Grep().arun({"pattern": "hello", "path": str(tmp_path), "include": "*.md"})
    files = {m["file"].split(str(tmp_path) + "/")[-1] for m in out["matches"]}
    assert files <= {"doc.md"}


@pytest.mark.asyncio
async def test_grep_literal(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("a.b.c\n", encoding="utf-8")
    # literal=False: '.' 是元字符匹配任意
    out_regex = await Grep().arun({"pattern": "a.b.c", "path": str(tmp_path)})
    assert out_regex["count"] >= 1
    # literal=True: 必须是字面字符串
    out_lit = await Grep().arun({"pattern": "a.b.c", "path": str(tmp_path), "literal": True})
    assert out_lit["count"] >= 1


@pytest.mark.asyncio
async def test_grep_no_match(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    out = await Grep().arun({"pattern": "no_such_pattern_xyz", "path": str(tmp_path)})
    assert out["ok"] is True
    assert out["count"] == 0


@pytest.mark.asyncio
async def test_grep_empty_pattern() -> None:
    out = await Grep().arun({"pattern": ""})
    assert out["ok"] is False


# ───── 强制走 Python 降级路径 (跳过 ripgrep) 测试 ─────


def test_python_fallback_directly(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    out = _run_python("hello", tmp_path, None, literal=False, ci=False, max_matches=100)
    assert out["ok"] is True
    assert out["engine"] == "python"
    assert out["count"] >= 3  # a.py + sub/c.py + doc.md


def test_python_fallback_truncation(tmp_path: Path) -> None:
    big = "\n".join([f"hello {i}" for i in range(50)]) + "\n"
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    out = _run_python("hello", tmp_path, None, literal=False, ci=False, max_matches=5)
    assert out["ok"] is True
    assert out["truncated"] is True
    assert out["count"] == 5
