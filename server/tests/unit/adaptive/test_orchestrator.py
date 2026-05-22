"""AdaptiveRunOrchestrator（M8）测试。"""

from __future__ import annotations

import subprocess

import pytest

from forge.adaptive.executor import ExecuteSummary
from forge.adaptive.integrator import IntegrationResult
from forge.adaptive.models import AdaptiveRun, Artifact, ArtifactKind, RunStatus
from forge.adaptive.options import HardCaps, TaskOptions
from forge.adaptive.orchestrator import AdaptiveRunOrchestrator
from forge.adaptive.planner import Planner
from forge.adaptive.store import AdaptiveRunStore
from forge.adaptive.validator import TaskGraphValidationError, TaskGraphValidator
from forge.adaptive.verifier import VerifyResult

pytestmark = pytest.mark.asyncio


def _options(workspace_path: str, *, max_replans: int) -> TaskOptions:
    return TaskOptions(
        allow_write=True,
        allow_parallel=True,
        max_agents=4,
        writer_mode="isolated_worktree",
        verifier_cmd=None,
        workspace_path=workspace_path,
        hard_caps=HardCaps(max_replans=max_replans),
    )


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    with open(f"{path}/README.md", "w", encoding="utf-8") as f:
        f.write("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)


async def test_plan_and_validate_replan_until_success(tmp_path) -> None:
    attempts = {"count": 0}

    def _plan_func(*_):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return """
            {
              "nodes": [
                {
                  "id": "t1",
                  "kind": "read",
                  "title": "bad",
                  "allowed_tools": ["unknown_tool"],
                  "read_scope": ["server"],
                  "write_scope": []
                }
              ]
            }
            """
        return """
        {
          "nodes": [
            {
              "id": "t1",
              "kind": "read",
              "title": "good",
              "allowed_tools": ["read_file"],
              "read_scope": ["server"],
              "write_scope": []
            }
          ]
        }
        """

    planner = Planner(plan_func=_plan_func)
    validator = TaskGraphValidator(tool_allowlist=["read_file"])
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    orchestrator = AdaptiveRunOrchestrator(
        planner=planner,
        validator=validator,
        store=store,
        tool_allowlist=["read_file"],
    )

    run = AdaptiveRun(run_id="run_replan_ok", workspace_path=str(tmp_path), goal="修复")
    await store.save_run(run)  # C6: 状态机硬约束要求先建档
    outcome = await orchestrator.plan_and_validate(
        run=run,
        options=_options(str(tmp_path), max_replans=2),
    )

    assert outcome.attempts == 2
    assert run.replan_count == 1
    assert run.status == RunStatus.VALIDATING
    events = await store.list_events(run.run_id)
    event_types = [evt.type for evt in events]
    assert "plan.rejected" in event_types
    assert "plan.validated" in event_types


async def test_plan_and_validate_blocked_after_replan_exhausted(tmp_path) -> None:
    planner = Planner(
        plan_func=lambda *_: """
        {
          "nodes": [
            {
              "id": "t1",
              "kind": "read",
              "title": "bad",
              "allowed_tools": ["unknown_tool"],
              "read_scope": ["server"],
              "write_scope": []
            }
          ]
        }
        """
    )
    validator = TaskGraphValidator(tool_allowlist=["read_file"])
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    orchestrator = AdaptiveRunOrchestrator(
        planner=planner,
        validator=validator,
        store=store,
        tool_allowlist=["read_file"],
    )
    run = AdaptiveRun(run_id="run_replan_fail", workspace_path=str(tmp_path), goal="修复")
    await store.save_run(run)  # C6: 状态机硬约束要求先建档

    with pytest.raises(TaskGraphValidationError):
        await orchestrator.plan_and_validate(
            run=run,
            options=_options(str(tmp_path), max_replans=1),
        )
    assert run.status == RunStatus.BLOCKED


async def test_run_full_flow_reaches_completed(tmp_path) -> None:
    _init_git_repo(str(tmp_path))
    planner = Planner()
    validator = TaskGraphValidator(
        tool_allowlist=[
            "list_directory",
            "glob_search",
            "read_file",
            "write_file",
            "edit_file",
            "run_tests",
            "shell",
        ]
    )
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)
    orchestrator = AdaptiveRunOrchestrator(
        planner=planner,
        validator=validator,
        store=store,
        tool_allowlist=[
            "list_directory",
            "glob_search",
            "read_file",
            "write_file",
            "edit_file",
            "run_tests",
            "shell",
        ],
    )
    run = AdaptiveRun(run_id="run_full_ok", workspace_path=str(tmp_path), goal="实现 M5 最小闭环")
    result = await orchestrator.run(run=run, options=_options(str(tmp_path), max_replans=1))
    assert result.status == RunStatus.COMPLETED
    assert len(result.artifact_ids) >= 2
    evt_types = [evt.type for evt in await store.list_events(run.run_id)]
    assert "run.completed" in evt_types


async def test_run_blocked_when_integration_conflict(tmp_path) -> None:
    _init_git_repo(str(tmp_path))
    planner = Planner()
    validator = TaskGraphValidator(
        tool_allowlist=[
            "list_directory",
            "glob_search",
            "read_file",
            "write_file",
            "edit_file",
            "run_tests",
            "shell",
        ]
    )
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)

    class _FakeExecutor:
        async def execute(self, *, run, options):  # type: ignore[no-untyped-def]
            _ = options
            # 人工塞一个 patch_set，便于后续流程持有 artifact。
            patch = Artifact(
                artifact_id="art_fake_patch",
                run_id=run.run_id,
                task_id="t_fake",
                kind=ArtifactKind.PATCH_SET,
                payload={"changed_files": ["server/a.py"], "diff": ""},
            )
            await store.save_artifact(patch)
            run.artifact_ids.append(patch.artifact_id)
            return ExecuteSummary(completed=1, failed=0, skipped=0)

    class _FakeIntegrator:
        async def merge(self, *, run, attempt=None):  # type: ignore[no-untyped-def]  # noqa: ARG002
            report = Artifact(
                artifact_id="art_int_report",
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.INTEGRATION_REPORT,
                payload={"merged": False},
            )
            conflict = Artifact(
                artifact_id="art_conflict",
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.CONFLICT_REPORT,
                payload={"type": "text_conflict"},
            )
            await store.save_artifact(report)
            await store.save_artifact(conflict)
            run.artifact_ids.extend([report.artifact_id, conflict.artifact_id])
            return IntegrationResult(
                merged=False,
                conflict=True,
                report_artifact=report,
                conflict_artifact=conflict,
                reason="text_conflict",
            )

    orchestrator = AdaptiveRunOrchestrator(
        planner=planner,
        validator=validator,
        executor=_FakeExecutor(),  # type: ignore[arg-type]
        integrator=_FakeIntegrator(),  # type: ignore[arg-type]
        store=store,
        tool_allowlist=[
            "list_directory",
            "glob_search",
            "read_file",
            "write_file",
            "edit_file",
            "run_tests",
            "shell",
        ],
    )
    run = AdaptiveRun(run_id="run_full_blocked", workspace_path=str(tmp_path), goal="冲突场景")
    result = await orchestrator.run(run=run, options=_options(str(tmp_path), max_replans=1))
    assert result.status == RunStatus.BLOCKED
    evt_types = [evt.type for evt in await store.list_events(run.run_id)]
    assert "integration.conflict" in evt_types
    assert "run.blocked" in evt_types


async def test_run_verify_fail_then_repair_pass(tmp_path) -> None:
    _init_git_repo(str(tmp_path))
    planner = Planner()
    validator = TaskGraphValidator(
        tool_allowlist=[
            "list_directory",
            "glob_search",
            "read_file",
            "write_file",
            "edit_file",
            "run_tests",
            "shell",
        ]
    )
    store = AdaptiveRunStore(workspace_path=tmp_path, base_dir=tmp_path)

    class _FakeVerifier:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, command, *, workspace_path, timeout_sec=600):  # type: ignore[no-untyped-def]
            _ = command, workspace_path, timeout_sec
            self.calls += 1
            if self.calls == 1:
                return VerifyResult(
                    passed=False,
                    command="fake",
                    returncode=1,
                    stdout="",
                    stderr="failed",
                    duration_ms=10,
                )
            return VerifyResult(
                passed=True,
                command="fake",
                returncode=0,
                stdout="ok",
                stderr="",
                duration_ms=10,
            )

    fake_verifier = _FakeVerifier()
    orchestrator = AdaptiveRunOrchestrator(
        planner=planner,
        validator=validator,
        store=store,
        verifier=fake_verifier,  # type: ignore[arg-type]
        tool_allowlist=[
            "list_directory",
            "glob_search",
            "read_file",
            "write_file",
            "edit_file",
            "run_tests",
            "shell",
        ],
    )
    run = AdaptiveRun(run_id="run_verify_repair", workspace_path=str(tmp_path), goal="验证失败修复")
    result = await orchestrator.run(
        run=run,
        options=TaskOptions(
            allow_write=True,
            allow_parallel=True,
            max_agents=4,
            writer_mode="isolated_worktree",
            verifier_cmd="fake verify",
            workspace_path=str(tmp_path),
            hard_caps=HardCaps(max_replans=2),
        ),
    )
    assert result.status == RunStatus.COMPLETED
    assert fake_verifier.calls == 2
    assert result.replan_count >= 1
    assert result.task_graph is not None
    assert any(task_id.startswith("fix_") for task_id in result.task_graph.nodes)
    evt_types = [evt.type for evt in await store.list_events(run.run_id)]
    assert "verify.failed" in evt_types
    assert "verify.passed" in evt_types
