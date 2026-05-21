"""Workspace 运行时设置解析."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.workspace.loader import load_workspace_context

_DEFAULT_CHAT_TIMEOUT_SECONDS = 300.0
_DEFAULT_WALL_CLOCK_SOFT_LIMIT = 120.0
_DEFAULT_WALL_CLOCK_WARN_LIMIT = 180.0
_DEFAULT_WALL_CLOCK_HARD_LIMIT = 240.0
_DEFAULT_SHELL_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class WorkspaceRuntimeSettings:
    chat_timeout_seconds: float = _DEFAULT_CHAT_TIMEOUT_SECONDS
    wall_clock_soft_limit_sec: float = _DEFAULT_WALL_CLOCK_SOFT_LIMIT
    wall_clock_warn_limit_sec: float = _DEFAULT_WALL_CLOCK_WARN_LIMIT
    wall_clock_hard_limit_sec: float = _DEFAULT_WALL_CLOCK_HARD_LIMIT
    tool_path_allowlist: tuple[str, ...] = (".",)
    shell_timeout_seconds: float = _DEFAULT_SHELL_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ToolRuntimePolicy:
    shell_timeout_seconds: float
    allowed_roots: tuple[Path, ...]


def resolve_runtime_settings(
    *,
    start: Path | None = None,
    raw_settings: dict[str, Any] | None = None,
) -> WorkspaceRuntimeSettings:
    """把 workspace settings 映射为运行时配置."""
    settings = raw_settings
    if settings is None:
        settings = load_workspace_context(start=start).settings

    chat_timeout = _as_positive_float(
        settings.get("chat_timeout_seconds"),
        _DEFAULT_CHAT_TIMEOUT_SECONDS,
    )
    soft = _as_positive_float(
        settings.get("chat_wall_clock_soft_limit_sec"),
        _DEFAULT_WALL_CLOCK_SOFT_LIMIT,
    )
    warn = _as_positive_float(
        settings.get("chat_wall_clock_warn_limit_sec"),
        _DEFAULT_WALL_CLOCK_WARN_LIMIT,
    )
    hard = _as_positive_float(
        settings.get("chat_wall_clock_hard_limit_sec"),
        _DEFAULT_WALL_CLOCK_HARD_LIMIT,
    )
    allowlist_raw = settings.get("tool_path_allowlist")
    if isinstance(allowlist_raw, list):
        allowlist = tuple(str(x) for x in allowlist_raw if isinstance(x, str) and x.strip())
    elif isinstance(allowlist_raw, str) and allowlist_raw.strip():
        allowlist = (allowlist_raw.strip(),)
    else:
        allowlist = (".",)
    shell_timeout = _as_positive_float(
        settings.get("shell_timeout_seconds"),
        _DEFAULT_SHELL_TIMEOUT_SECONDS,
    )
    if not (0 < soft < warn < hard):
        soft = _DEFAULT_WALL_CLOCK_SOFT_LIMIT
        warn = _DEFAULT_WALL_CLOCK_WARN_LIMIT
        hard = _DEFAULT_WALL_CLOCK_HARD_LIMIT
    return WorkspaceRuntimeSettings(
        chat_timeout_seconds=chat_timeout,
        wall_clock_soft_limit_sec=soft,
        wall_clock_warn_limit_sec=warn,
        wall_clock_hard_limit_sec=hard,
        tool_path_allowlist=allowlist,
        shell_timeout_seconds=shell_timeout,
    )


def resolve_tool_runtime_policy(
    *,
    start: Path | None = None,
    raw_settings: dict[str, Any] | None = None,
) -> ToolRuntimePolicy:
    """解析工具执行策略 (路径白名单 + shell 超时)."""
    start_path = (start or Path.cwd()).expanduser().resolve()
    if raw_settings is None:
        ctx = load_workspace_context(start=start_path)
        settings = ctx.settings
        base_root = ctx.root_path if ctx.mode == "workspace" else start_path
    else:
        settings = raw_settings
        base_root = start_path

    runtime = resolve_runtime_settings(
        start=start_path,
        raw_settings=settings,
    )
    roots: list[Path] = []
    for raw in runtime.tool_path_allowlist:
        p = Path(raw).expanduser()
        resolved = (p if p.is_absolute() else (base_root / p)).resolve()
        roots.append(resolved)
    if not roots:
        roots = [base_root.resolve()]
    return ToolRuntimePolicy(
        shell_timeout_seconds=runtime.shell_timeout_seconds,
        allowed_roots=tuple(roots),
    )


def _as_positive_float(value: Any, default: float) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if x <= 0:
        return default
    return x
