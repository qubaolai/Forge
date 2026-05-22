"""TaskExecutor 串行执行测试。"""

from __future__ import annotations

import asyncio
import subprocess
import time

import pytest

from forge.adaptive.executor import TaskExecutor
from forge.adaptive.models import (
    AdaptiveRun,
    Artifact,
    ArtifactKind,
    TaskGraph,
    TaskKind,
    TaskNode,
    TaskStatus,
)
from forge.adaptive.options import HardCaps, TaskOptions
from forge.adaptive.store import AdaptiveRunStore

pytestmark = pytest.mark.asyncio


def _options(workspace_path: str) -> TaskOptions:
    return TaskOptions(
        allow_write=True,
        allow_parallel=True,
        max_agents=4,
        writer_mode="isolated_worktree",
        verifier_cmd=None,
        workspace_path=workspace_path,
        hard_caps=HardCaps(),
    )


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    with open(f"{path}/README.md", "w", encoding="utf-8") as f:
        f.write("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)


async def test_executor_runs_three_nodes_serially(tmp_path) -> None:
    _init_git_repo(str(tmp_path))
    graph = TaskGraph(
        nodes={
            "t1": TaskNode(
                id="t1",
                title="读",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
                output_contract="discovery_report",
            ),
            "t2": TaskNode(
                id="t2",
                title="写",
                kind=TaskKind.WRITE,
                allowed_tools=("write_file",),
                read_scope=("server",),
                write_scope=("server",),
                deps=("t1",),
                output_contract="patch_set",
            ),
            "t3": TaskNode(
                id="t3",
                title="测",
                kind=TaskKind.EXECUTE,
                allowed_tools=("run_tests",),
                read_scope=("server",),
                write_scope=(),
                deps=("t2",),
                output_contract="test_report",
            ),
        }
    )
    run = AdaptiveRun(
        run_id="run_exec_ok",
        workspace_path=str(tmp_path),
        goal="实现并验证",
        task_graph=graph,
    )
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    await store.save_run(run)

    executor = TaskExecutor(store=store)
    summary = await executor.execute(run=run, options=_options(str(tmp_path)))

    assert summary.completed == 3
    assert summary.failed == 0
    assert summary.skipped == 0
    assert run.task_graph is not None
    assert all(node.status == TaskStatus.COMPLETED for node in run.task_graph.nodes.values())
    assert len(run.artifact_ids) == 3


async def test_executor_skips_when_dependency_failed(tmp_path) -> None:
    # t1 写任务缺少必须字段，执行时通过人工异常制造失败。
    graph = TaskGraph(
        nodes={
            "t1": TaskNode(
                id="t1",
                title="故意失败",
                kind=TaskKind.EXECUTE,
                allowed_tools=("run_tests",),
                read_scope=("server",),
                write_scope=(),
                output_contract="test_report",
            ),
            "t2": TaskNode(
                id="t2",
                title="应被跳过",
                kind=TaskKind.WRITE,
                allowed_tools=("write_file",),
                read_scope=("server",),
                write_scope=("server",),
                deps=("t1",),
                output_contract="patch_set",
            ),
        }
    )
    run = AdaptiveRun(
        run_id="run_exec_skip",
        workspace_path=str(tmp_path),
        goal="测试跳过",
        task_graph=graph,
    )
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    await store.save_run(run)
    executor = TaskExecutor(store=store)

    # monkey patch: 让 t1 执行抛错，验证 t2 被跳过。
    original = executor._execute_single_node

    async def _boom(*, run, node, graph, options):  # type: ignore[no-untyped-def]
        if node.id == "t1":
            raise RuntimeError("模拟失败")
        return await original(run=run, node=node, graph=graph, options=options)

    executor._execute_single_node = _boom  # type: ignore[method-assign]
    summary = await executor.execute(run=run, options=_options(str(tmp_path)))

    assert summary.failed == 1
    assert summary.skipped == 1
    assert run.task_graph is not None
    assert run.task_graph.nodes["t1"].status == TaskStatus.FAILED
    assert run.task_graph.nodes["t2"].status == TaskStatus.SKIPPED


async def test_executor_runs_tasks_in_same_wave_concurrently(tmp_path) -> None:
    _init_git_repo(str(tmp_path))
    graph = TaskGraph(
        nodes={
            "t1": TaskNode(
                id="t1",
                title="read-1",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
                output_contract="discovery_report",
            ),
            "t2": TaskNode(
                id="t2",
                title="read-2",
                kind=TaskKind.READ,
                allowed_tools=("read_file",),
                read_scope=("server",),
                write_scope=(),
                output_contract="discovery_report",
            ),
        }
    )
    run = AdaptiveRun(
        run_id="run_exec_parallel",
        workspace_path=str(tmp_path),
        goal="并发",
        task_graph=graph,
    )
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    await store.save_run(run)
    executor = TaskExecutor(store=store)

    async def _slow_execute_single_node(*, run, node, graph, options):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.2)
        return Artifact(
            artifact_id=f"art_{node.id}",
            run_id=run.run_id,
            task_id=node.id,
            kind=ArtifactKind.DISCOVERY_REPORT,
            payload={"task_id": node.id},
        )

    executor._execute_single_node = _slow_execute_single_node  # type: ignore[method-assign]
    t0 = time.perf_counter()
    summary = await executor.execute(run=run, options=_options(str(tmp_path)))
    elapsed = time.perf_counter() - t0
    # 串行约 0.4s，并发应显著小于 0.35s。
    assert elapsed < 0.35
    assert summary.completed == 2
