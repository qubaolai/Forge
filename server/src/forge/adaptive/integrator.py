"""PatchSet 合并器（M7）。"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.adaptive.models import AdaptiveRun, Artifact, ArtifactKind
from forge.adaptive.store import AdaptiveRunStore
from forge.utils.id_generator import new_id


@dataclass(frozen=True)
class IntegrationResult:
    """集成结果。"""

    merged: bool
    conflict: bool
    report_artifact: Artifact
    conflict_artifact: Artifact | None = None
    reason: str = ""


class Integrator:
    """串行 PatchSet 合并器。"""

    def __init__(self, *, store: AdaptiveRunStore | None = None) -> None:
        self._store = store

    async def merge(
        self,
        *,
        run: AdaptiveRun,
        attempt: int | None = None,
    ) -> IntegrationResult:
        """合并 PatchSet artifact.

        B6/P0-4: ``attempt`` 非空时仅合并 ``payload.attempt == attempt`` 的 patch；
        ``run.metadata['integrated_patch_ids']`` 中已记录的 patch 也会被排除，
        避免修复循环重复 apply 历史补丁。
        """
        if self._store is None:
            raise RuntimeError("Integrator 需要 store 才能读取 artifacts")

        patch_sets = await self._store.list_artifacts(run.run_id, kind=ArtifactKind.PATCH_SET)
        already_integrated = set(run.metadata.get("integrated_patch_ids", []) or [])
        if already_integrated:
            patch_sets = [p for p in patch_sets if p.artifact_id not in already_integrated]
        if attempt is not None:
            patch_sets = [
                p for p in patch_sets
                if int((p.payload or {}).get("attempt", 0)) == attempt
            ]
        conflicts = _detect_text_conflicts(patch_sets)
        if conflicts:
            conflict_art = Artifact(
                artifact_id=new_id("art"),
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.CONFLICT_REPORT,
                payload={
                    "type": "text_conflict",
                    "conflicts": conflicts,
                },
            )
            await self._store.save_artifact(conflict_art)
            run.artifact_ids.append(conflict_art.artifact_id)

            report = Artifact(
                artifact_id=new_id("art"),
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.INTEGRATION_REPORT,
                payload={
                    "merged": False,
                    "reason": "text_conflict",
                    "patch_count": len(patch_sets),
                    "conflict_report_id": conflict_art.artifact_id,
                },
            )
            await self._store.save_artifact(report)
            run.artifact_ids.append(report.artifact_id)
            return IntegrationResult(
                merged=False,
                conflict=True,
                report_artifact=report,
                conflict_artifact=conflict_art,
                reason="text_conflict",
            )

        # B10/P1-5: 原子集成 —— 先 dry-run (git apply --check) 所有 patch，
        # 全部通过才真实 apply。任一 check 失败立即上报冲突，不污染工作区。
        non_empty: list[tuple[Artifact, str]] = [
            (patch, str((patch.payload or {}).get("diff") or ""))
            for patch in patch_sets
            if str((patch.payload or {}).get("diff") or "").strip()
        ]
        precheck_errors: list[str] = []
        for patch, diff in non_empty:
            err = await _apply_diff(run.workspace_path, diff, check_only=True)
            if err:
                precheck_errors.append(f"{patch.artifact_id}: {err}")
        if precheck_errors:
            conflict_art = Artifact(
                artifact_id=new_id("art"),
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.CONFLICT_REPORT,
                payload={
                    "type": "apply_conflict",
                    "phase": "precheck",
                    "errors": precheck_errors,
                },
            )
            await self._store.save_artifact(conflict_art)
            run.artifact_ids.append(conflict_art.artifact_id)
            report = Artifact(
                artifact_id=new_id("art"),
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.INTEGRATION_REPORT,
                payload={
                    "merged": False,
                    "reason": "apply_conflict",
                    "phase": "precheck",
                    "patch_count": len(patch_sets),
                    "errors": precheck_errors,
                    "conflict_report_id": conflict_art.artifact_id,
                },
            )
            await self._store.save_artifact(report)
            run.artifact_ids.append(report.artifact_id)
            return IntegrationResult(
                merged=False,
                conflict=True,
                report_artifact=report,
                conflict_artifact=conflict_art,
                reason="apply_conflict",
            )

        apply_errors: list[str] = []
        for patch, diff in non_empty:
            err = await _apply_diff(run.workspace_path, diff)
            if err:
                apply_errors.append(f"{patch.artifact_id}: {err}")

        if apply_errors:
            conflict_art = Artifact(
                artifact_id=new_id("art"),
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.CONFLICT_REPORT,
                payload={
                    "type": "apply_conflict",
                    "errors": apply_errors,
                },
            )
            await self._store.save_artifact(conflict_art)
            run.artifact_ids.append(conflict_art.artifact_id)
            report = Artifact(
                artifact_id=new_id("art"),
                run_id=run.run_id,
                task_id=None,
                kind=ArtifactKind.INTEGRATION_REPORT,
                payload={
                    "merged": False,
                    "reason": "apply_conflict",
                    "patch_count": len(patch_sets),
                    "errors": apply_errors,
                    "conflict_report_id": conflict_art.artifact_id,
                },
            )
            await self._store.save_artifact(report)
            run.artifact_ids.append(report.artifact_id)
            return IntegrationResult(
                merged=False,
                conflict=True,
                report_artifact=report,
                conflict_artifact=conflict_art,
                reason="apply_conflict",
            )

        applied_patch_ids = [patch.artifact_id for patch in patch_sets]
        report = Artifact(
            artifact_id=new_id("art"),
            run_id=run.run_id,
            task_id=None,
            kind=ArtifactKind.INTEGRATION_REPORT,
            payload={
                "merged": True,
                "reason": "ok",
                "patch_count": len(patch_sets),
                "attempt": attempt,
                "applied_patch_ids": applied_patch_ids,
            },
        )
        await self._store.save_artifact(report)
        run.artifact_ids.append(report.artifact_id)
        # B6/P0-4: 累积记录已集成 patch id，下一轮修复跳过
        prior = list(run.metadata.get("integrated_patch_ids", []) or [])
        run.metadata["integrated_patch_ids"] = prior + applied_patch_ids
        return IntegrationResult(
            merged=True,
            conflict=False,
            report_artifact=report,
            conflict_artifact=None,
            reason="ok",
        )


def _detect_text_conflicts(patch_sets: list[Artifact]) -> list[dict[str, Any]]:
    owners: dict[str, list[str]] = {}
    for patch in patch_sets:
        changed = (patch.payload or {}).get("changed_files", [])
        if not isinstance(changed, list):
            continue
        for file_path in changed:
            key = str(file_path).strip()
            if not key:
                continue
            owners.setdefault(key, []).append(patch.artifact_id)

    conflicts: list[dict[str, Any]] = []
    for path, artifact_ids in owners.items():
        if len(artifact_ids) > 1:
            conflicts.append({"file": path, "artifact_ids": artifact_ids})
    return sorted(conflicts, key=lambda x: x["file"])


async def _apply_diff(
    workspace_path: str,
    diff_text: str,
    *,
    check_only: bool = False,
) -> str | None:
    """对 workspace_path 应用 unified diff。

    ``check_only=True`` 时只跑 ``git apply --check``（不写盘），用于 B10 原子集成 dry-run。
    成功返回 None，失败返回错误信息字符串。
    """
    import asyncio

    workspace = Path(workspace_path).expanduser().resolve()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".patch") as f:
        f.write(diff_text)
        patch_file = f.name

    git_args: list[str] = ["apply", "--whitespace=nowarn"]
    if check_only:
        git_args.append("--check")
    else:
        # 真正 apply 不带 --reject，确保失败时不会留半截 .rej 文件
        # （precheck 已保证可 apply 成功）
        pass
    git_args.append(patch_file)

    proc = await asyncio.create_subprocess_exec(
        "git",
        *git_args,
        cwd=str(workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    Path(patch_file).unlink(missing_ok=True)
    if proc.returncode == 0:
        return None
    stdout = stdout_b.decode("utf-8", errors="replace").strip()
    stderr = stderr_b.decode("utf-8", errors="replace").strip()
    return stderr or stdout or "git apply 失败"
