"""glob_search 单测."""

from __future__ import annotations

import os
import time
from pathlib import Path

from forge.tools.builtin.file.glob_search import GlobSearch


def test_glob_matches_pattern(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.py").write_text("y", encoding="utf-8")
    (tmp_path / "c.txt").write_text("z", encoding="utf-8")
    out = GlobSearch().run({"pattern": "*.py", "base": str(tmp_path)})
    assert out["ok"] is True
    files = {f["relative"] for f in out["files"]}
    assert files == {"a.py", "b.py"}


def test_glob_recursive(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("x", encoding="utf-8")
    (tmp_path / "top.py").write_text("y", encoding="utf-8")
    out = GlobSearch().run({"pattern": "**/*.py", "base": str(tmp_path)})
    rel = {f["relative"] for f in out["files"]}
    assert "top.py" in rel
    assert "sub/deep.py" in rel


def test_glob_skips_noise(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    (tmp_path / "src.py").write_text("y", encoding="utf-8")
    out = GlobSearch().run({"pattern": "**/*", "base": str(tmp_path)})
    rel = {f["relative"] for f in out["files"]}
    assert "src.py" in rel
    assert not any(".git" in r for r in rel)


def test_glob_sort_by_mtime_desc(tmp_path: Path) -> None:
    a = tmp_path / "old.py"
    a.write_text("x", encoding="utf-8")
    time.sleep(0.05)
    b = tmp_path / "new.py"
    b.write_text("y", encoding="utf-8")
    # 显式设 mtime
    os.utime(a, (100, 100))
    os.utime(b, (200, 200))
    out = GlobSearch().run({"pattern": "*.py", "base": str(tmp_path)})
    assert out["files"][0]["relative"] == "new.py"
    assert out["files"][1]["relative"] == "old.py"


def test_glob_max_results(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("x", encoding="utf-8")
    out = GlobSearch().run({"pattern": "*.py", "base": str(tmp_path), "max_results": 3})
    assert out["truncated"] is True
    assert len(out["files"]) == 3


def test_glob_empty_pattern() -> None:
    out = GlobSearch().run({"pattern": "  "})
    assert out["ok"] is False
