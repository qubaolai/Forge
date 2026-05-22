"""写隔离策略（M6）。"""

from __future__ import annotations

import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from forge.adaptive.models import AdaptiveRun, TaskNode


@dataclass(frozen=True)
class IsolatedEnv:
    """隔离执行环境信息。"""

    strategy: str
    work_path: str
    base_ref: str
    workspace_path: str


@dataclass(frozen=True)
class PatchSetPayload:
    """PatchSet 结构化载荷。"""

    task_id: str
    base_ref: str
    worktree_path: str
    changed_files: list[str]
    diff: str

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "base_ref": self.base_ref,
            "worktree_path": self.worktree_path,
            "changed_files": list(self.changed_files),
            "diff": self.diff,
        }


class IsolateStrategy(Protocol):
    async def prepare(self, node: TaskNode, run: AdaptiveRun) -> IsolatedEnv: ...
    async def collect(self, env: IsolatedEnv, *, task_id: str) -> PatchSetPayload: ...
    async def cleanup(self, env: IsolatedEnv) -> None: ...


class GitWorktreeStrategy:
    """基于 git worktree 的写隔离策略。"""

    async def prepare(self, node: TaskNode, run: AdaptiveRun) -> IsolatedEnv:
        workspace = Path(run.workspace_path).expanduser().resolve()
        await _ensure_git_repo(workspace)
        base_ref = (await _run_git(workspace, "rev-parse", "HEAD")).strip()
        work_path = Path(tempfile.gettempdir()) / (
            f"forge-{run.run_id[:8]}-{node.id}-{secrets.token_hex(3)}"
        )
        await _run_git(workspace, "worktree", "add", "--detach", str(work_path), base_ref)
        return IsolatedEnv(
            strategy="worktree",
            work_path=str(work_path),
            base_ref=base_ref,
            workspace_path=str(workspace),
        )

    async def collect(self, env: IsolatedEnv, *, task_id: str) -> PatchSetPayload:
        worktree = Path(env.work_path).expanduser().resolve()
        changed_raw = await _run_git(worktree, "diff", "--name-only", env.base_ref)
        diff = await _run_git(worktree, "diff", env.base_ref)
        changed_files = [line.strip() for line in changed_raw.splitlines() if line.strip()]
        return PatchSetPayload(
            task_id=task_id,
            base_ref=env.base_ref,
            worktree_path=str(worktree),
            changed_files=changed_files,
            diff=diff,
        )

    async def cleanup(self, env: IsolatedEnv) -> None:
        workspace = Path(env.workspace_path).expanduser().resolve()
        worktree = Path(env.work_path).expanduser().resolve()
        try:
            await _run_git(workspace, "worktree", "remove", "--force", str(worktree))
        except RuntimeError:
            # 元数据清理失败时兜底删除目录，避免 /tmp 残留。
            shutil.rmtree(worktree, ignore_errors=True)


async def _ensure_git_repo(workspace: Path) -> None:
    if not workspace.exists():
        raise RuntimeError(f"workspace 不存在: {workspace}")
    try:
        out = (await _run_git(workspace, "rev-parse", "--is-inside-work-tree")).strip().lower()
    except RuntimeError as exc:
        raise RuntimeError(f"workspace 不是 git 仓库: {workspace}") from exc
    if out != "true":
        raise RuntimeError(f"workspace 不是 git 仓库: {workspace}")


async def _run_git(cwd: Path, *args: str) -> str:
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 执行失败: {stderr.strip() or stdout.strip()}")
    return stdout
