"""工具权限检查防护栏.

职责
----
工具调用前的 RBAC 闸门:
    - 按 ``role × tool`` 矩阵决定该角色能否调该工具
    - 按 ``role × path_prefix`` 矩阵决定该角色能否在某路径上动手 (read_file /
      write_file / shell 的 ``cwd`` 等)
    - 路径白名单从 ``<workspace>/.assistant/settings.json`` 的
      ``path_role_whitelist`` 字段读

输入 / 输出
-----------
``check(tool_name, role, args) -> CheckResult``:
    - allow=True  -> 放行
    - allow=False -> 含 reason, 调用方决定拒绝并写入 audit log

设计选择:
    - 不动 ``Tool`` / ``ToolExecutor`` 的接口; 由 executor 在执行前显式调.
    - 缺省 role = "local" (单机模式默认拥有所有权限); 多 Agent 改造时由
      ReActAgent 传入实际 role.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认 role -> 允许工具集. 空集 = 不限制 (单机宽松默认).
_DEFAULT_ROLE_TOOL_ALLOWLIST: dict[str, set[str]] = {
    "local": set(),
    "owner": set(),
    "triage": {
        "knowledge_search",
        "read_file",
        "list_directory",
        "glob_search",
        "grep",
        "get_artifact",
        "search_artifact",
        "delegate_to_agent",
        "spawn_subagent",
    },
    "ra": {
        "knowledge_search",
        "read_file",
        "list_directory",
        "glob_search",
        "grep",
        "create_artifact",
        "get_artifact",
        "search_artifact",
        "delegate_to_agent",
        "spawn_subagent",
    },
    "architect": {
        "knowledge_search",
        "read_file",
        "list_directory",
        "glob_search",
        "grep",
        "git_ops",
        "create_artifact",
        "get_artifact",
        "search_artifact",
        "delegate_to_agent",
        "spawn_subagent",
    },
    "developer": {
        "knowledge_search",
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "glob_search",
        "grep",
        "git_ops",
        "shell",
        "http_request",
        "create_artifact",
        "get_artifact",
        "search_artifact",
        "delegate_to_agent",
        "spawn_subagent",
    },
    "reviewer": {
        "knowledge_search",
        "read_file",
        "list_directory",
        "glob_search",
        "grep",
        "git_ops",
        "create_artifact",
        "get_artifact",
        "search_artifact",
        "delegate_to_agent",
        "spawn_subagent",
    },
    "qa": {
        "knowledge_search",
        "read_file",
        "list_directory",
        "glob_search",
        "grep",
        "shell",
        "create_artifact",
        "get_artifact",
        "search_artifact",
        "delegate_to_agent",
        "spawn_subagent",
    },
    "devops": {
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "glob_search",
        "grep",
        "git_ops",
        "shell",
        "http_request",
        "create_artifact",
        "get_artifact",
        "search_artifact",
        "delegate_to_agent",
        "spawn_subagent",
    },
}

_DEFAULT_PATH_ROLE_WHITELIST: dict[str, list[str]] = {
    "developer": ["src/", "app/", "lib/"],
    "qa": ["tests/"],
    "devops": [".github/", "docker/", "infra/"],
}


@dataclass(frozen=True)
class CheckResult:
    allow: bool
    reason: str = ""


class PermissionChecker:
    """工具权限检查器, 无状态."""

    def __init__(
        self,
        *,
        role_tool_allowlist: dict[str, set[str]] | None = None,
        path_role_whitelist: dict[str, list[str]] | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._role_tool_allowlist = role_tool_allowlist or _DEFAULT_ROLE_TOOL_ALLOWLIST
        self._path_role_whitelist = path_role_whitelist or _DEFAULT_PATH_ROLE_WHITELIST
        self._workspace_root = workspace_root

    def check(
        self,
        tool_name: str,
        role: str,
        args: dict[str, Any] | None = None,
    ) -> CheckResult:
        # 1. role × tool 矩阵
        allowed = self._role_tool_allowlist.get(role)
        if allowed is None:
            pass  # 未配置的 role 默认放行
        elif allowed and tool_name not in allowed:
            return CheckResult(
                allow=False,
                reason=f"角色 {role!r} 无权调用工具 {tool_name!r}",
            )

        # 2. role × path 矩阵 (仅针对涉及路径的工具)
        if args and self._path_role_whitelist:
            path_arg = self._extract_path(tool_name, args)
            if path_arg is not None:
                allowed_prefixes = self._path_role_whitelist.get(role)
                if allowed_prefixes is not None and not self._path_under(
                    path_arg, allowed_prefixes
                ):
                    return CheckResult(
                        allow=False,
                        reason=(
                            f"角色 {role!r} 不允许在路径 {path_arg!r} 上操作; "
                            f"允许前缀: {allowed_prefixes}"
                        ),
                    )
        return CheckResult(allow=True)

    @staticmethod
    def _extract_path(tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name in {"write_file", "edit_file"}:
            v = args.get("path")
            return v if isinstance(v, str) else None
        if tool_name == "shell":
            v = args.get("cwd")
            return v if isinstance(v, str) else None
        if tool_name == "git_ops":
            v = args.get("cwd")
            return v if isinstance(v, str) else None
        return None

    def _path_under(self, path_str: str, prefixes: list[str]) -> bool:
        p = Path(path_str).expanduser()
        if not p.is_absolute() and self._workspace_root is not None:
            p = (self._workspace_root / p).resolve()
        else:
            p = p.resolve()
        for prefix in prefixes:
            pref_path = Path(prefix).expanduser()
            if not pref_path.is_absolute() and self._workspace_root is not None:
                pref_path = (self._workspace_root / pref_path).resolve()
            else:
                pref_path = pref_path.resolve()
            if p == pref_path or pref_path in p.parents:
                return True
        return False


__all__ = ["CheckResult", "PermissionChecker"]
