"""按内容搜代码的工具.

实现策略:
    - 优先调用系统 ``rg`` (ripgrep) — 快, 自带 .gitignore 支持.
    - 找不到 ``rg`` 时降级到纯 Python 实现 (``re`` + ``os.walk``).
    - 两条路径返回相同 schema.

行为:
    - 默认正则匹配; ``literal=True`` 时按字面字符串.
    - ``include`` (glob) 限定文件类型, 例如 ``"*.py"``.
    - ``path`` 限定搜索根 (默认 cwd).
    - 返回每条匹配的 ``file / line / text``, 总数截到 ``max_matches`` (默认 200).

安全:
    - 路径越界由 ToolExecutor 兜底.
    - 命令不通过 shell, 用 ``asyncio.create_subprocess_exec`` 避免注入.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_MAX_MATCHES = 200

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
class Grep(Tool):
    name = "grep"
    description = (
        "在文件内容里搜模式. 优先用 ripgrep (rg), 不可用时降级到 Python re. "
        "返回每条匹配的 file / line / text."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "正则模式 (literal=true 时按字面字符串).",
            },
            "path": {
                "type": "string",
                "description": "搜索根, 默认 cwd.",
            },
            "include": {
                "type": "string",
                "description": "限定文件 glob, 例如 '*.py' 或 '**/*.ts'.",
            },
            "literal": {
                "type": "boolean",
                "description": "true: 按字面匹配 (转义正则元字符). 默认 false.",
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "忽略大小写. 默认 false.",
            },
            "max_matches": {
                "type": "integer",
                "description": f"返回匹配上限. 默认 {_DEFAULT_MAX_MATCHES}.",
                "minimum": 1,
            },
        },
        "required": ["pattern"],
    }
    required_scope = "workspace"
    timeout_sec = 30.0

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        pattern: str = args["pattern"]
        path_str: str = args.get("path") or "."
        include: str | None = args.get("include")
        literal: bool = bool(args.get("literal", False))
        ci: bool = bool(args.get("case_insensitive", False))
        max_matches: int = int(args.get("max_matches") or _DEFAULT_MAX_MATCHES)

        if not pattern:
            return {"ok": False, "error": "pattern 不能为空"}

        base = Path(path_str).expanduser()
        base = (Path.cwd() / base).resolve() if not base.is_absolute() else base.resolve()
        if not base.exists() or not base.is_dir():
            return {"ok": False, "path": str(base), "error": "搜索根不是目录"}

        rg = shutil.which("rg")
        if rg:
            return await _run_ripgrep(rg, pattern, base, include, literal, ci, max_matches)
        return await asyncio.to_thread(
            _run_python, pattern, base, include, literal, ci, max_matches
        )


async def _run_ripgrep(
    rg: str,
    pattern: str,
    base: Path,
    include: str | None,
    literal: bool,
    ci: bool,
    max_matches: int,
) -> dict[str, Any]:
    cmd = [rg, "--line-number", "--no-heading", "--color=never", "-S"]
    if literal:
        cmd.append("-F")
    if ci:
        cmd.append("-i")
    cmd.extend(["-m", str(max_matches)])
    if include:
        cmd.extend(["-g", include])
    cmd.append(pattern)
    cmd.append(str(base))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except (TimeoutError, OSError) as exc:
        return {"ok": False, "error": f"rg 执行失败: {exc}"}

    # rg returncode: 0=有匹配, 1=无匹配, 2=出错
    matches: list[dict[str, Any]] = []
    for line in stdout_b.decode("utf-8", errors="replace").splitlines():
        # 格式: "<path>:<lineno>:<text>"
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        file_path, lineno_str, text = parts
        try:
            lineno = int(lineno_str)
        except ValueError:
            continue
        matches.append({"file": file_path, "line": lineno, "text": text})
        if len(matches) >= max_matches:
            break

    return {
        "ok": True,
        "engine": "ripgrep",
        "base": str(base),
        "pattern": pattern,
        "count": len(matches),
        "truncated": len(matches) >= max_matches,
        "matches": matches,
    }


def _run_python(
    pattern: str,
    base: Path,
    include: str | None,
    literal: bool,
    ci: bool,
    max_matches: int,
) -> dict[str, Any]:
    try:
        regex = re.compile(
            re.escape(pattern) if literal else pattern,
            flags=re.IGNORECASE if ci else 0,
        )
    except re.error as exc:
        return {"ok": False, "error": f"正则编译失败: {exc}"}

    matches: list[dict[str, Any]] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIR_NAMES]
        root_path = Path(root)
        for fname in files:
            fpath = root_path / fname
            if include and not _matches_glob(fpath, base, include):
                continue
            try:
                with fpath.open("r", encoding="utf-8", errors="replace") as f:
                    for ln, line_text in enumerate(f, start=1):
                        if regex.search(line_text):
                            matches.append(
                                {
                                    "file": str(fpath),
                                    "line": ln,
                                    "text": line_text.rstrip("\n"),
                                }
                            )
                            if len(matches) >= max_matches:
                                return {
                                    "ok": True,
                                    "engine": "python",
                                    "base": str(base),
                                    "pattern": pattern,
                                    "count": len(matches),
                                    "truncated": True,
                                    "matches": matches,
                                }
            except OSError:
                continue
    return {
        "ok": True,
        "engine": "python",
        "base": str(base),
        "pattern": pattern,
        "count": len(matches),
        "truncated": False,
        "matches": matches,
    }


def _matches_glob(p: Path, base: Path, pattern: str) -> bool:
    """按相对 base 的路径匹配 glob."""
    try:
        rel = p.relative_to(base)
    except ValueError:
        rel = p
    return Path(rel).match(pattern) or Path(p.name).match(pattern)
