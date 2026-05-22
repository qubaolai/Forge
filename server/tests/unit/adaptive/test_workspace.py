"""GitWorktreeStrategy 测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge.adaptive.models import AdaptiveRun, TaskKind, TaskNode
from forge.adaptive.workspace import GitWorktreeStrategy

pytestmark = pytest.mark.asyncio


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(path), check=True)
    (path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path),
        check=True,
        capture_output=True,
        text=True,
    )


async def test_worktree_prepare_collect_cleanup(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    run = AdaptiveRun(run_id="run_ws_1", workspace_path=str(tmp_path), goal="g")
    node = TaskNode(
        id="t_write",
        title="写任务",
        kind=TaskKind.WRITE,
        allowed_tools=("write_file",),
        read_scope=("server",),
        write_scope=("server",),
    )
    strategy = GitWorktreeStrategy()

    env = await strategy.prepare(node, run)
    worktree = Path(env.work_path)
    assert worktree.exists()
    # 在隔离目录里制造一个改动，验证 collect 能抓到 patch。
    (worktree / "README.md").write_text("changed\n", encoding="utf-8")

    patch = await strategy.collect(env, task_id=node.id)
    assert patch.task_id == node.id
    assert "README.md" in patch.diff
    assert "README.md" in patch.changed_files

    await strategy.cleanup(env)
    assert not worktree.exists()


async def test_worktree_prepare_requires_git_repo(tmp_path: Path) -> None:
    run = AdaptiveRun(run_id="run_ws_2", workspace_path=str(tmp_path), goal="g")
    node = TaskNode(
        id="t_write",
        title="写任务",
        kind=TaskKind.WRITE,
        allowed_tools=("write_file",),
        read_scope=("server",),
        write_scope=("server",),
    )
    strategy = GitWorktreeStrategy()
    with pytest.raises(RuntimeError, match="git 仓库"):
        await strategy.prepare(node, run)
