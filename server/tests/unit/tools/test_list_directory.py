"""list_directory 单测."""

from __future__ import annotations

from pathlib import Path

from forge.tools.builtin.file.list_directory import ListDirectory


def test_lists_files_and_dirs(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.py").write_text("y", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    out = ListDirectory().run({"path": str(tmp_path)})
    assert out["ok"] is True
    names = [e["name"] for e in out["entries"]]
    assert "sub/" in names
    assert "a.txt" in names
    assert "b.py" in names
    # 目录排在前面
    assert names[0] == "sub/"


def test_skips_noise_dirs(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()
    out = ListDirectory().run({"path": str(tmp_path)})
    names = [e["name"] for e in out["entries"]]
    assert "src/" in names
    assert ".git/" not in names
    assert "node_modules/" not in names


def test_recursive_max_depth(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "c.txt").write_text("x", encoding="utf-8")
    out = ListDirectory().run({"path": str(tmp_path), "recursive": True, "max_depth": 2})
    names = [e["name"] for e in out["entries"]]
    assert "a/" in names
    assert "a/b/" in names
    # 深度=2, c.txt 在 depth 3, 不包含
    assert "a/b/c.txt" not in names


def test_hidden_files(tmp_path: Path) -> None:
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("y", encoding="utf-8")

    out = ListDirectory().run({"path": str(tmp_path)})
    names = [e["name"] for e in out["entries"]]
    assert ".hidden" not in names
    assert "visible.txt" in names

    out = ListDirectory().run({"path": str(tmp_path), "include_hidden": True})
    names = [e["name"] for e in out["entries"]]
    assert ".hidden" in names


def test_path_not_dir(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    out = ListDirectory().run({"path": str(f)})
    assert out["ok"] is False
    assert "不是目录" in out["error"]


def test_truncation(tmp_path: Path) -> None:
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    out = ListDirectory().run({"path": str(tmp_path), "max_entries": 5})
    assert out["truncated"] is True
    assert len(out["entries"]) == 5
