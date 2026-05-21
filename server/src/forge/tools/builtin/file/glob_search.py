"""按 glob 模式查找文件.

行为:
    - 支持 ``**`` 递归通配, 例如 ``src/**/*.py``.
    - 默认从 cwd 出发; 可显式 ``base`` 切换搜索根.
    - 跳过常见噪音目录 (.git / node_modules / __pycache__ 等).
    - 结果按修改时间倒序 (常用的"最近改过的文件"模式).
    - 截断到 ``max_results``.

路径越界由 ToolExecutor 兜底.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RESULTS = 200

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
class GlobSearch(Tool):
    name = "glob_search"
    description = (
        "按 glob 模式查找文件路径, 支持 ** 递归通配 (例如 src/**/*.py). "
        "结果按修改时间倒序. 适合定位文件; 内容搜索用 grep."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "glob 模式, 例如 '**/*.py' 或 'src/**/*.tsx'.",
            },
            "base": {
                "type": "string",
                "description": "搜索根目录, 默认 cwd.",
            },
            "max_results": {
                "type": "integer",
                "description": f"返回上限. 默认 {_DEFAULT_MAX_RESULTS}.",
                "minimum": 1,
            },
        },
        "required": ["pattern"],
    }
    required_scope = "workspace"

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        pattern: str = args["pattern"]
        base_str: str = args.get("base") or "."
        max_results: int = int(args.get("max_results") or _DEFAULT_MAX_RESULTS)

        if not pattern.strip():
            return {"ok": False, "error": "pattern 不能为空"}

        base = Path(base_str).expanduser()
        base = (Path.cwd() / base).resolve() if not base.is_absolute() else base.resolve()
        if not base.exists() or not base.is_dir():
            return {"ok": False, "base": str(base), "error": "base 不是目录"}

        matches: list[tuple[float, Path]] = []
        for p in base.glob(pattern):
            if any(part in _SKIP_DIR_NAMES for part in p.parts):
                continue
            if not p.is_file():
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            matches.append((mtime, p))

        matches.sort(reverse=True, key=lambda x: x[0])
        truncated = len(matches) > max_results
        matches = matches[:max_results]

        return {
            "ok": True,
            "base": str(base),
            "pattern": pattern,
            "count": len(matches),
            "truncated": truncated,
            "files": [
                {
                    "path": str(p),
                    "relative": str(p.relative_to(base)),
                    "mtime": mtime,
                }
                for mtime, p in matches
            ],
        }
