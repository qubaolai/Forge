"""TaskGraph 校验器（M4）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from forge.adaptive.models import TaskGraph, TaskKind
from forge.adaptive.options import TaskOptions

# 显式写工具集：直接产生文件变更
WRITE_TOOLS = {"write_file", "edit_file", "git_ops"}
# 副作用工具集：可能写文件（shell 执行任意命令、run_tests 可能改 cache 等）
SIDE_EFFECT_TOOLS = {"shell", "run_tests"}
# READ 任务禁用的工具集合 = 写工具 + 副作用工具
READ_FORBIDDEN_TOOLS = WRITE_TOOLS | SIDE_EFFECT_TOOLS


@dataclass(frozen=True)
class ValidationIssue:
    """单条校验问题。"""

    code: str
    message: str
    node_id: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    """校验结果。"""

    valid: bool
    issues: tuple[ValidationIssue, ...] = ()


class TaskGraphValidationError(ValueError):
    """TaskGraph 校验失败异常。"""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        msg = "; ".join(f"{i.code}:{i.message}" for i in issues) or "task graph 校验失败"
        super().__init__(msg)


class TaskGraphValidator:
    """TaskGraph 规则校验。"""

    def __init__(self, *, tool_allowlist: list[str]) -> None:
        self._tool_allowlist = set(tool_allowlist)

    def validate(self, graph: TaskGraph, *, options: TaskOptions) -> ValidationResult:
        """执行规则校验。"""
        issues: list[ValidationIssue] = []
        nodes = graph.nodes

        issues.extend(self._check_dep_exists(nodes))
        issues.extend(self._check_dag_acyclic(nodes))
        issues.extend(self._check_tools_allowlist(nodes))
        issues.extend(self._check_scope_within_workspace(nodes, workspace_path=options.workspace_path))
        issues.extend(self._check_read_write_constraints(nodes))
        issues.extend(self._check_max_steps(nodes, max_steps=options.hard_caps.max_steps_per_task))
        # B3/P1-7: allow_write=False 时强制拒绝任何写节点 / 写工具
        if not options.allow_write:
            issues.extend(self._check_allow_write_constraint(nodes))
        # 只有前置 DAG/dep 校验通过时，才有可靠 wave 结果。
        if not any(i.code in {"deps_missing", "dag_cycle"} for i in issues):
            issues.extend(self._check_same_wave_write_conflicts(nodes, workspace_path=options.workspace_path))
        return ValidationResult(valid=(len(issues) == 0), issues=tuple(issues))

    def _check_allow_write_constraint(self, nodes: dict[str, object]) -> list[ValidationIssue]:
        """B3/P1-7: allow_write=False 时禁止任何写任务。"""
        issues: list[ValidationIssue] = []
        for node_id, node in nodes.items():
            kind = node.kind
            tools = set(getattr(node, "allowed_tools", ()))
            write_scope = tuple(getattr(node, "write_scope", ()))

            if kind in (TaskKind.WRITE, TaskKind.INTEGRATE):
                issues.append(
                    ValidationIssue(
                        code="write_disabled_kind",
                        node_id=node_id,
                        message=f"allow_write=False 下不允许 {kind.value} 任务",
                    )
                )
            if write_scope:
                issues.append(
                    ValidationIssue(
                        code="write_disabled_scope",
                        node_id=node_id,
                        message="allow_write=False 下不允许声明 write_scope",
                    )
                )
            forbidden = tools & WRITE_TOOLS
            if forbidden:
                issues.append(
                    ValidationIssue(
                        code="write_disabled_tool",
                        node_id=node_id,
                        message=f"allow_write=False 下禁止写工具: {sorted(forbidden)}",
                    )
                )
        return issues

    def assert_valid(self, graph: TaskGraph, *, options: TaskOptions) -> None:
        result = self.validate(graph, options=options)
        if not result.valid:
            raise TaskGraphValidationError(list(result.issues))

    def _check_dep_exists(self, nodes: dict[str, object]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        node_ids = set(nodes.keys())
        for node_id, node in nodes.items():
            for dep in getattr(node, "deps", ()):
                if dep not in node_ids:
                    issues.append(
                        ValidationIssue(
                            code="deps_missing",
                            node_id=node_id,
                            message=f"依赖任务不存在: {dep}",
                        )
                    )
        return issues

    def _check_dag_acyclic(self, nodes: dict[str, object]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        temp: set[str] = set()
        perm: set[str] = set()
        has_cycle = False

        def visit(node_id: str) -> None:
            nonlocal has_cycle
            if node_id in perm or has_cycle:
                return
            if node_id in temp:
                has_cycle = True
                return
            temp.add(node_id)
            node = nodes[node_id]
            for dep in getattr(node, "deps", ()):
                if dep in nodes:
                    visit(dep)
            temp.remove(node_id)
            perm.add(node_id)

        for node_id in nodes:
            visit(node_id)
        if has_cycle:
            issues.append(ValidationIssue(code="dag_cycle", message="任务图存在环"))
        return issues

    def _check_tools_allowlist(self, nodes: dict[str, object]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for node_id, node in nodes.items():
            for tool in getattr(node, "allowed_tools", ()):
                if tool not in self._tool_allowlist:
                    issues.append(
                        ValidationIssue(
                            code="tool_not_allowed",
                            node_id=node_id,
                            message=f"工具不在 allowlist: {tool}",
                        )
                    )
        return issues

    def _check_scope_within_workspace(
        self,
        nodes: dict[str, object],
        *,
        workspace_path: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        root = Path(workspace_path).expanduser().resolve()
        for node_id, node in nodes.items():
            for raw in getattr(node, "write_scope", ()):
                normalized = _resolve_scope_path(root, raw)
                if not _is_subpath(root, normalized):
                    issues.append(
                        ValidationIssue(
                            code="write_scope_out_of_root",
                            node_id=node_id,
                            message=f"write_scope 越界: {raw}",
                        )
                    )
        return issues

    def _check_read_write_constraints(self, nodes: dict[str, object]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for node_id, node in nodes.items():
            kind = node.kind
            tools = set(getattr(node, "allowed_tools", ()))
            write_scope = tuple(getattr(node, "write_scope", ()))

            if kind == TaskKind.READ:
                if write_scope:
                    issues.append(
                        ValidationIssue(
                            code="read_has_write_scope",
                            node_id=node_id,
                            message="READ 任务不能声明 write_scope",
                        )
                    )
                # N14: READ 任务禁止写工具 + 副作用工具（shell/run_tests 可能产生文件变更）
                forbidden = tools & READ_FORBIDDEN_TOOLS
                if forbidden:
                    issues.append(
                        ValidationIssue(
                            code="read_has_write_tool",
                            node_id=node_id,
                            message=f"READ 任务不能使用写/副作用工具: {sorted(forbidden)}",
                        )
                    )

            if kind == TaskKind.WRITE and not write_scope:
                issues.append(
                    ValidationIssue(
                        code="write_scope_required",
                        node_id=node_id,
                        message="WRITE 任务必须声明 write_scope",
                    )
                )

            # N15: EXECUTE 任务不应声明 write_scope（只跑命令、不直接改源码）
            if kind == TaskKind.EXECUTE and write_scope:
                issues.append(
                    ValidationIssue(
                        code="execute_has_write_scope",
                        node_id=node_id,
                        message="EXECUTE 任务不能声明 write_scope",
                    )
                )

            # N15: INTEGRATE 任务必须依赖至少一个其他任务（合并谁？），
            # 且 write_scope 强制非空（合并目标必须有作用域）
            if kind == TaskKind.INTEGRATE:
                if not getattr(node, "deps", ()):
                    issues.append(
                        ValidationIssue(
                            code="integrate_missing_deps",
                            node_id=node_id,
                            message="INTEGRATE 任务必须声明 deps",
                        )
                    )
                if not write_scope:
                    issues.append(
                        ValidationIssue(
                            code="integrate_missing_write_scope",
                            node_id=node_id,
                            message="INTEGRATE 任务必须声明合并目标 write_scope",
                        )
                    )
        return issues

    def _check_max_steps(self, nodes: dict[str, object], *, max_steps: int) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for node_id, node in nodes.items():
            node_max_steps = int(getattr(node, "max_steps", 0))
            # N13: 下限校验，避免 0/负数任务被调度
            if node_max_steps < 1:
                issues.append(
                    ValidationIssue(
                        code="max_steps_invalid",
                        node_id=node_id,
                        message=f"max_steps={node_max_steps} 必须 >= 1",
                    )
                )
                continue
            if node_max_steps > max_steps:
                issues.append(
                    ValidationIssue(
                        code="max_steps_exceeded",
                        node_id=node_id,
                        message=f"max_steps={node_max_steps} 超过硬上限 {max_steps}",
                    )
                )
        return issues

    def _check_same_wave_write_conflicts(
        self,
        nodes: dict[str, object],
        *,
        workspace_path: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        root = Path(workspace_path).expanduser().resolve()
        waves = _compute_waves(nodes)
        for wave_nodes in waves.values():
            writers = []
            for node_id in wave_nodes:
                node = nodes[node_id]
                scopes = tuple(getattr(node, "write_scope", ()))
                if not scopes:
                    continue
                resolved = tuple(_resolve_scope_path(root, scope) for scope in scopes)
                writers.append((node_id, resolved))
            for idx, (left_id, left_scopes) in enumerate(writers):
                for right_id, right_scopes in writers[idx + 1 :]:
                    if _scope_overlap(left_scopes, right_scopes):
                        issues.append(
                            ValidationIssue(
                                code="same_wave_write_conflict",
                                node_id=left_id,
                                message=f"与任务 {right_id} 的 write_scope 冲突",
                            )
                        )
        return issues


def _resolve_scope_path(root: Path, raw_scope: str) -> Path:
    p = Path(raw_scope).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (root / p).resolve()


def _is_subpath(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _scope_overlap(left: tuple[Path, ...], right: tuple[Path, ...]) -> bool:
    for p1 in left:
        for p2 in right:
            if _is_subpath(p1, p2) or _is_subpath(p2, p1):
                return True
    return False


def _compute_waves(nodes: dict[str, object]) -> dict[int, list[str]]:
    memo: dict[str, int] = {}

    def level(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        deps = tuple(getattr(nodes[node_id], "deps", ()))
        if not deps:
            memo[node_id] = 0
            return 0
        value = max(level(dep) for dep in deps if dep in nodes) + 1
        memo[node_id] = value
        return value

    waves: dict[int, list[str]] = {}
    for node_id in nodes:
        lv = level(node_id)
        waves.setdefault(lv, []).append(node_id)
    return waves
