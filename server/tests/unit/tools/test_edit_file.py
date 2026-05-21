"""edit_file 工具单测."""

from __future__ import annotations

from pathlib import Path

from forge.tools.builtin.file.edit_file import EditFile


def test_edit_unique_match(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("def hello():\n    return 1\n", encoding="utf-8")
    out = EditFile().run({"path": str(f), "old_string": "return 1", "new_string": "return 42"})
    assert out["ok"] is True
    assert out["replacements"] == 1
    assert f.read_text(encoding="utf-8") == "def hello():\n    return 42\n"


def test_edit_rejects_non_unique_match(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\ny = 1\n", encoding="utf-8")
    out = EditFile().run({"path": str(f), "old_string": "= 1", "new_string": "= 2"})
    assert out["ok"] is False
    assert out["matches"] == 2
    assert "唯一" in out["error"] or "replace_all" in out["error"]


def test_edit_replace_all_passes(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\ny = 1\n", encoding="utf-8")
    out = EditFile().run(
        {
            "path": str(f),
            "old_string": "= 1",
            "new_string": "= 2",
            "replace_all": True,
        }
    )
    assert out["ok"] is True
    assert out["replacements"] == 2
    assert f.read_text(encoding="utf-8") == "x = 2\ny = 2\n"


def test_edit_missing_old_string(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("hello\n", encoding="utf-8")
    out = EditFile().run({"path": str(f), "old_string": "world", "new_string": "x"})
    assert out["ok"] is False
    assert out["matches"] == 0


def test_edit_same_old_new(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("hello\n", encoding="utf-8")
    out = EditFile().run({"path": str(f), "old_string": "x", "new_string": "x"})
    assert out["ok"] is False
    assert "相同" in out["error"]


def test_edit_file_not_exists(tmp_path: Path) -> None:
    out = EditFile().run(
        {
            "path": str(tmp_path / "nope.txt"),
            "old_string": "x",
            "new_string": "y",
        }
    )
    assert out["ok"] is False
    assert "不存在" in out["error"]
