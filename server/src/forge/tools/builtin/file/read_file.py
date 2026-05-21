"""读取本地文件内容的工具.

设计:
    - 路径越界由 ``ToolExecutor._apply_workspace_policy`` 兜底, 工具自身只做执行.
    - 默认 UTF-8 解码, 失败时回退到二进制摘要 (前 1KB hex + 文件大小).
    - 支持按行范围 (``offset`` / ``limit``) 读取大文件, 避免上下文炸开.
    - 单次输出截断到 ``max_chars`` 字符 (默认 100KB), 防 LLM 上下文超限.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CHARS = 100_000
_BINARY_PREVIEW_BYTES = 1024


@register_tool
class ReadFile(Tool):
    name = "read_file"
    description = (
        "读取本地文件内容. 支持按行号范围读取大文件 (offset/limit), "
        "默认 UTF-8 解码, 二进制文件返回前 1KB 十六进制摘要."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径 (绝对或相对当前工作目录)",
            },
            "offset": {
                "type": "integer",
                "description": "起始行号 (1-based, 含). 不传则从头读.",
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "最多读取行数. 不传则读到文件末尾.",
                "minimum": 1,
            },
            "max_chars": {
                "type": "integer",
                "description": f"输出字符数上限. 默认 {_DEFAULT_MAX_CHARS}.",
                "minimum": 100,
            },
        },
        "required": ["path"],
    }
    parallelism_safe = True

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        path_str: str = args["path"]
        offset: int | None = args.get("offset")
        limit: int | None = args.get("limit")
        max_chars: int = int(args.get("max_chars") or _DEFAULT_MAX_CHARS)

        target = Path(path_str).expanduser()
        target = (Path.cwd() / target).resolve() if not target.is_absolute() else target.resolve()

        if not target.exists():
            return {"ok": False, "path": str(target), "error": "文件不存在"}
        if not target.is_file():
            return {"ok": False, "path": str(target), "error": "不是普通文件"}

        size_bytes = target.stat().st_size

        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            preview = target.read_bytes()[:_BINARY_PREVIEW_BYTES].hex()
            return {
                "ok": True,
                "path": str(target),
                "binary": True,
                "size_bytes": size_bytes,
                "hex_preview": preview,
            }

        lines = text.splitlines(keepends=True)
        total_lines = len(lines)

        if offset is not None or limit is not None:
            start = (offset or 1) - 1
            end = total_lines if limit is None else start + limit
            sliced = lines[start:end]
            content = "".join(sliced)
            first_line = start + 1
            last_line = start + len(sliced)
        else:
            content = text
            first_line = 1 if total_lines else 0
            last_line = total_lines

        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        return {
            "ok": True,
            "path": str(target),
            "binary": False,
            "size_bytes": size_bytes,
            "total_lines": total_lines,
            "first_line": first_line,
            "last_line": last_line,
            "truncated": truncated,
            "content": content,
        }
