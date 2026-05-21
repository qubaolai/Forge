"""git_ops 单测.

约束: 需要系统有 git. 没装 git 时跳过 (用 shutil.which 探测).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from forge.tools.builtin.code.git_ops import GitOps

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "f.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        env={
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )


@pytest.mark.asyncio
async def test_git_status(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    out = await GitOps().arun({"op": "status", "args": ["--short"], "cwd": str(tmp_path)})
    assert out["ok"] is True
    assert out["exit_code"] == 0


@pytest.mark.asyncio
async def test_git_log(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    out = await GitOps().arun({"op": "log", "args": ["--oneline", "-n", "5"], "cwd": str(tmp_path)})
    assert out["ok"] is True
    assert "init" in out["stdout"]


@pytest.mark.asyncio
async def test_git_diff_no_changes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    out = await GitOps().arun({"op": "diff", "cwd": str(tmp_path)})
    assert out["ok"] is True
    assert out["stdout"] == ""


@pytest.mark.asyncio
async def test_git_rejects_unknown_op() -> None:
    out = await GitOps().arun({"op": "push"})
    assert out["ok"] is False
    assert "白名单" in out["error"]


@pytest.mark.asyncio
async def test_git_rejects_disallowed_param(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    out = await GitOps().arun(
        {
            "op": "log",
            "args": ["--upload-pack=/tmp/evil"],
            "cwd": str(tmp_path),
        }
    )
    assert out["ok"] is False
    assert "禁用" in out["error"] or "允许" in out["error"]


@pytest.mark.asyncio
async def test_git_rejects_unknown_flag(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    out = await GitOps().arun(
        {
            "op": "status",
            "args": ["--unknown-flag"],
            "cwd": str(tmp_path),
        }
    )
    assert out["ok"] is False
    assert "允许" in out["error"]


@pytest.mark.asyncio
async def test_git_branch_show_current(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    out = await GitOps().arun({"op": "branch", "args": ["--show-current"], "cwd": str(tmp_path)})
    assert out["ok"] is True
    assert out["stdout"].strip() == "main"
