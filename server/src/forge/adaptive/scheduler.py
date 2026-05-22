"""任务调度器（M9: wave 并行 + 写冲突拆分）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from forge.adaptive.models import TaskGraph


@dataclass(frozen=True)
class SchedulePlan:
    """调度计划。"""

    waves: tuple[tuple[str, ...], ...]

    @property
    def ordered_task_ids(self) -> tuple[str, ...]:
        out: list[str] = []
        for wave in self.waves:
            out.extend(wave)
        return tuple(out)


class Scheduler:
    """根据依赖关系计算 wave。"""

    def compute_waves(self, graph: TaskGraph, *, workspace_path: str | None = None) -> SchedulePlan:
        nodes = graph.nodes
        memo: dict[str, int] = {}

        def _level(task_id: str) -> int:
            if task_id in memo:
                return memo[task_id]
            deps = tuple(dep for dep in nodes[task_id].deps if dep in nodes)
            if not deps:
                memo[task_id] = 0
                return 0
            lv = max(_level(dep) for dep in deps) + 1
            memo[task_id] = lv
            return lv

        wave_map: dict[int, list[str]] = {}
        for task_id in nodes:
            lv = _level(task_id)
            wave_map.setdefault(lv, []).append(task_id)

        root = Path(workspace_path).expanduser().resolve() if workspace_path else None
        waves_list: list[tuple[str, ...]] = []
        for lv in sorted(wave_map.keys()):
            groups = _split_wave_by_write_conflict(
                sorted(wave_map[lv]),
                nodes=nodes,
                root=root,
            )
            waves_list.extend(groups)

        waves = tuple(waves_list)
        return SchedulePlan(waves=waves)


def _split_wave_by_write_conflict(
    task_ids: list[str],
    *,
    nodes: dict[str, object],
    root: Path | None,
) -> list[tuple[str, ...]]:
    buckets: list[list[str]] = []
    for task_id in task_ids:
        placed = False
        for bucket in buckets:
            if not _task_conflicts_with_bucket(task_id, bucket, nodes=nodes, root=root):
                bucket.append(task_id)
                placed = True
                break
        if not placed:
            buckets.append([task_id])
    return [tuple(bucket) for bucket in buckets]


def _task_conflicts_with_bucket(
    task_id: str,
    bucket: list[str],
    *,
    nodes: dict[str, object],
    root: Path | None,
) -> bool:
    for other_id in bucket:
        if _task_write_conflict(task_id, other_id, nodes=nodes, root=root):
            return True
    return False


def _task_write_conflict(a: str, b: str, *, nodes: dict[str, object], root: Path | None) -> bool:
    left = _resolved_scopes(nodes[a], root)
    right = _resolved_scopes(nodes[b], root)
    if not left or not right:
        return False
    for p1 in left:
        for p2 in right:
            if _is_subpath(p1, p2) or _is_subpath(p2, p1):
                return True
    return False


def _resolved_scopes(node: object, root: Path | None) -> tuple[Path, ...]:
    scopes = tuple(getattr(node, "write_scope", ()) or ())
    if not scopes:
        return ()
    if root is None:
        return tuple(Path(str(s)).expanduser() for s in scopes)
    resolved: list[Path] = []
    for scope in scopes:
        p = Path(str(scope)).expanduser()
        if p.is_absolute():
            resolved.append(p.resolve())
        else:
            resolved.append((root / p).resolve())
    return tuple(resolved)


def _is_subpath(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
