"""写本地文件的工具.

设计:
    - 路径越界由 ``ToolExecutor._apply_workspace_policy`` 兜底, 工具不重复校验路径.
    - 默认覆盖写; ``mode='append'`` 时追加.
    - 自动建父目录 (``parents=True``).
    - **有副作用**, ``parallelism_safe=False``, ReActAgent 一个 step 内多个
      write_file 串行执行避免互相冲突.
    - 写入大小限制 (``max_bytes``, 默认 5MB), 防 LLM 失控写超大文件.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


@register_tool
class WriteFile(Tool):
    name = "write_file"
    description = (
        "向本地文件写入内容. 默认覆盖写, mode='append' 追加. "
        "自动创建父目录. 路径必须落在 workspace 允许范围内."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径 (绝对或相对 cwd)",
            },
            "content": {
                "type": "string",
                "description": "要写入的文本内容 (UTF-8 编码)",
            },
            "mode": {
                "type": "string",
                "description": "写入模式: write (默认覆盖) / append (追加)",
                "enum": ["write", "append"],
            },
            "max_bytes": {
                "type": "integer",
                "description": f"单次写入字节上限. 默认 {_DEFAULT_MAX_BYTES}.",
                "minimum": 1,
            },
        },
        "required": ["path", "content"],
    }
    parallelism_safe = False
    dangerous = True
    audit_payload_fields = ("path", "mode")
    timeout_sec = 10.0
    required_scope = "workspace"

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        path_str: str = args["path"]
        content: str = args["content"]
        mode: str = args.get("mode") or "write"
        max_bytes: int = int(args.get("max_bytes") or _DEFAULT_MAX_BYTES)

        if mode not in ("write", "append"):
            return {"ok": False, "error": f"非法 mode: {mode}"}

        encoded = content.encode("utf-8")
        if len(encoded) > max_bytes:
            return {
                "ok": False,
                "error": f"内容超过 max_bytes ({len(encoded)} > {max_bytes})",
            }

        target = Path(path_str).expanduser()
        target = (Path.cwd() / target).resolve() if not target.is_absolute() else target.resolve()

        target.parent.mkdir(parents=True, exist_ok=True)
        flag = "w" if mode == "write" else "a"
        existed = target.exists()
        size_before = target.stat().st_size if existed else 0

        try:
            with target.open(flag, encoding="utf-8") as f:
                f.write(content)
        except OSError as exc:
            return {"ok": False, "error": f"写入失败: {exc}"}

        size_after = target.stat().st_size
        logger.info(
            "工具写入文件 path=%s mode=%s bytes=%d existed=%s",
            target,
            mode,
            len(encoded),
            existed,
        )
        return {
            "ok": True,
            "path": str(target),
            "mode": mode,
            "existed": existed,
            "bytes_written": len(encoded),
            "size_before": size_before,
            "size_after": size_after,
        }
