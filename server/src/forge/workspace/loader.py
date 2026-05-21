"""Workspace 上下文加载.

S3 阶段目标:
1. 从当前目录向上查找 ``.assistant/`` 作为 workspace 根.
2. 读取 ``workspace.json`` / ``settings.json`` / ``settings.local.json`` 与 ``ASSISTANT.md``.
3. 找不到 workspace 时, 回退到全局兜底目录.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import paths


@dataclass(frozen=True)
class WorkspaceContext:
    """一次加载得到的 workspace 视图."""

    mode: str  # "workspace" | "global"
    root_path: Path
    assistant_prompt: str
    settings: dict[str, Any] = field(default_factory=dict)


def load_workspace_context(start: Path | None = None) -> WorkspaceContext:
    """加载 workspace 配置.

    Args:
        start: 查找起点. None 时使用当前工作目录.
    """
    start_path = (start or Path.cwd()).expanduser().resolve()
    root = paths.find_workspace_root(start_path)
    if root is not None:
        return WorkspaceContext(
            mode="workspace",
            root_path=root,
            assistant_prompt=_read_text(paths.workspace_assistant_path(root)),
            settings=_merge_settings(
                _read_json(paths.workspace_config_path(root)),
                _read_json(paths.workspace_settings_path(root)),
                _read_json(paths.workspace_local_settings_path(root)),
            ),
        )

    # 无 workspace 时走全局兜底模式.
    return WorkspaceContext(
        mode="global",
        root_path=paths.global_workspace_dir(),
        assistant_prompt=_read_text(paths.global_workspace_assistant_path()),
        settings=_merge_settings(
            _read_json(paths.global_workspace_config_path()),
            _read_json(paths.global_workspace_settings_path()),
            _read_json(paths.global_workspace_local_settings_path()),
        ),
    )


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _merge_settings(*parts: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for p in parts:
        merged.update(p)
    return merged
