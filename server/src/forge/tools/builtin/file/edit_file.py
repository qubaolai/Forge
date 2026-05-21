"""字符串精确替换的编辑工具.

为什么不直接用 write_file:
    - 让 LLM 重写整文件费 token; 多人协作时也容易踩对方代码.
    - edit_file 强制 ``old_string`` 唯一匹配, 拒绝多匹配, 防误改.

行为
----
- ``old_string`` 必须在文件中 **恰好出现一次**, 否则报错并提示匹配数.
- ``replace_all=True`` 时跳过唯一性检查, 替换全部匹配.
- ``old_string == new_string`` 报错 (避免无效编辑).
- 文件不存在或不是普通文件直接报错.
- 输出含 ``replacements`` 计数 + 替换前后的行号范围, 让 LLM 自检.

安全
----
路径越界 / 系统目录由 ToolExecutor + DangerousOpBlocker 兜底. 工具自身只做执行.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 5 * 1024 * 1024


@register_tool
class EditFile(Tool):
    name = "edit_file"
    description = (
        "对文件做字符串级精确替换. 默认 old_string 必须唯一匹配, replace_all=true "
        "时替换全部. 返回 replacements 计数. 适合小范围修改, 整文件重写用 write_file."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目标文件路径 (绝对或相对 cwd)",
            },
            "old_string": {
                "type": "string",
                "description": "要被替换的子串. 默认要求文件中恰好出现一次.",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的子串. 必须与 old_string 不同.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "true: 替换全部匹配, 跳过唯一性检查. 默认 false.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }

    parallelism_safe = False
    dangerous = True
    audit_payload_fields = ("path", "replace_all")
    timeout_sec = 10.0
    required_scope = "workspace"

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        path_str: str = args["path"]
        old: str = args["old_string"]
        new: str = args["new_string"]
        replace_all: bool = bool(args.get("replace_all", False))

        if old == new:
            return {"ok": False, "error": "old_string 与 new_string 相同, 不做修改"}
        if not old:
            return {"ok": False, "error": "old_string 不能为空"}

        target = Path(path_str).expanduser()
        target = (Path.cwd() / target).resolve() if not target.is_absolute() else target.resolve()

        if not target.exists():
            return {"ok": False, "path": str(target), "error": "文件不存在"}
        if not target.is_file():
            return {"ok": False, "path": str(target), "error": "不是普通文件"}

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "path": str(target), "error": "无法以 UTF-8 读取 (二进制文件)"}

        count = content.count(old)
        if count == 0:
            return {
                "ok": False,
                "path": str(target),
                "error": "old_string 在文件中未找到",
                "matches": 0,
            }
        if count > 1 and not replace_all:
            return {
                "ok": False,
                "path": str(target),
                "error": (
                    f"old_string 匹配到 {count} 处, 请扩大上下文使其唯一, 或设置 replace_all=true"
                ),
                "matches": count,
            }

        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)

        if len(new_content.encode("utf-8")) > _DEFAULT_MAX_BYTES:
            return {
                "ok": False,
                "error": f"替换后文件超过 {_DEFAULT_MAX_BYTES} 字节, 拒绝写入",
            }

        try:
            target.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"写入失败: {exc}"}

        replacements = count if replace_all else 1
        logger.info(
            "工具编辑文件 path=%s replacements=%d replace_all=%s",
            target,
            replacements,
            replace_all,
        )
        return {
            "ok": True,
            "path": str(target),
            "replacements": replacements,
            "replace_all": replace_all,
            "size_before": len(content.encode("utf-8")),
            "size_after": len(new_content.encode("utf-8")),
        }
