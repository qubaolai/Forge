"""列目录工具.

行为:
    - 默认非递归, ``recursive=True`` 时按 ``max_depth`` 递归 (默认 3).
    - 跳过常见噪音目录 (``.git`` / ``node_modules`` / ``__pycache__`` 等),
      ``include_hidden=True`` 时不跳隐藏文件.
    - 返回项含 type / size_bytes / name; 目录最后挂 ``/``.
    - 总条目截断到 ``max_entries`` (默认 500), 防大目录把上下文炸了.

路径越界由 ToolExecutor 兜底校验.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 500
_DEFAULT_MAX_DEPTH = 3

_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".next",
        ".turbo",
        ".cache",
    }
)


@register_tool
class ListDirectory(Tool):
    name = "list_directory"
    description = (
        "列目录内容. 默认非递归, recursive=true 时按 max_depth 递归. "
        "自动跳过 .git/node_modules/__pycache__ 等噪音目录."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径 (绝对或相对 cwd). 默认当前目录.",
            },
            "recursive": {
                "type": "boolean",
                "description": "是否递归. 默认 false.",
            },
            "max_depth": {
                "type": "integer",
                "description": f"递归深度上限. 默认 {_DEFAULT_MAX_DEPTH}.",
                "minimum": 1,
            },
            "include_hidden": {
                "type": "boolean",
                "description": "是否含点开头的隐藏文件. 默认 false.",
            },
            "max_entries": {
                "type": "integer",
                "description": f"返回条目数上限. 默认 {_DEFAULT_MAX_ENTRIES}.",
                "minimum": 1,
            },
        },
        "required": [],
    }
    required_scope = "workspace"

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        path_str: str = args.get("path") or "."
        recursive: bool = bool(args.get("recursive", False))
        max_depth: int = int(args.get("max_depth") or _DEFAULT_MAX_DEPTH)
        include_hidden: bool = bool(args.get("include_hidden", False))
        max_entries: int = int(args.get("max_entries") or _DEFAULT_MAX_ENTRIES)

        root = Path(path_str).expanduser()
        root = (Path.cwd() / root).resolve() if not root.is_absolute() else root.resolve()

        if not root.exists():
            return {"ok": False, "path": str(root), "error": "目录不存在"}
        if not root.is_dir():
            return {"ok": False, "path": str(root), "error": "不是目录"}

        entries: list[dict[str, Any]] = []
        truncated = False
        for entry in _walk(root, root, recursive, max_depth, include_hidden, 0):
            if len(entries) >= max_entries:
                truncated = True
                break
            entries.append(entry)

        return {
            "ok": True,
            "path": str(root),
            "count": len(entries),
            "truncated": truncated,
            "entries": entries,
        }


def _walk(
    base: Path,
    cur: Path,
    recursive: bool,
    max_depth: int,
    include_hidden: bool,
    depth: int,
):
    try:
        children = sorted(cur.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except (PermissionError, OSError):
        return
    for child in children:
        name = child.name
        if not include_hidden and name.startswith("."):
            continue
        if child.is_dir() and name in _SKIP_DIR_NAMES:
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        rel = str(child.relative_to(base))
        is_dir = child.is_dir()
        yield {
            "name": rel + ("/" if is_dir else ""),
            "type": "dir" if is_dir else "file",
            "size_bytes": stat.st_size if not is_dir else None,
        }
        if recursive and is_dir and depth + 1 < max_depth:
            yield from _walk(base, child, recursive, max_depth, include_hidden, depth + 1)
