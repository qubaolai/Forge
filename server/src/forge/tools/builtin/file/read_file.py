"""read_file 工具: 按 file_id 读取会话文件内容 (支持 line_range 切片).

配合输入侧附件机制: 用户大段输入被转为会话文件 + [file:<id>] 占位,
LLM 需要时调本工具按需 (可选 line_range) 读取, 避免整段灌入上下文爆窗。
鉴权: 读 current_user_id, 仅允许读取归属该用户的会话文件 (越权当作未找到)。
"""

from __future__ import annotations

from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool


def _parse_line_range(value: Any) -> tuple[int, int] | None:
    """把 [start, end] 解析成 (start, end); 非法返回 None (= 取全文)。"""
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    try:
        start, end = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    if start <= 0 or end <= 0:
        return None
    return start, end


@register_tool
class ReadFile(Tool):
    name = "read_file"
    description = (
        "按 file_id 读取会话文件内容 (纯文本/代码)。当用户输入中出现 [file:<id>] 引用占位 (大段输入/附件) 时, "
        "用本工具读取其完整内容; 可选 line_range=[起始行,结束行] (1-based 闭区间) 只取片段, 避免整段拉回。"
        "返回字段: text / total_lines / returned_range / truncated。"
        "注意: Word/Excel/PDF 等二进制文档请改用 read_document (本工具只能读纯文本)。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "文件 ID, 即引用占位 [file:<id>] 中的 id",
            },
            "line_range": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "(可选) 1-based 闭区间 [起始行, 结束行]; 不传则返回全文",
            },
        },
        "required": ["file_id"],
    }
    parallelism_safe = True

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        from forge.core.request_context import current_user_id
        from forge.infrastructure.storage.content_store import slice_text

        from ._access import resolve_owned_file

        file_id = str(args.get("file_id") or "").strip()
        if not file_id:
            return {"ok": False, "error": "缺少 file_id"}

        # 鉴权: 与 read_message 一致, 读 current_user_id; 缺用户上下文一律拒绝。
        user_id = current_user_id() or ""
        if not user_id:
            return {"ok": False, "error": "缺少用户上下文, 拒绝执行"}

        # 读文件不限来源: 先查用户上传 (user_files), 未命中再查生成沙盒 (chat_files)。
        resolved = await resolve_owned_file(file_id, user_id)
        # 越权一律当作「未找到」(不泄露存在性)
        if resolved is None:
            return {"ok": False, "error": f"文件未找到或无权访问: {file_id}"}
        filename, storage, storage_path = resolved

        try:
            content = storage.read_text(storage_path)
        except (FileNotFoundError, ValueError, OSError):
            return {"ok": False, "error": f"文件内容读取失败: {file_id}"}

        line_range = _parse_line_range(args.get("line_range"))
        sl = slice_text(content, line_range)
        return {
            "ok": True,
            "file_id": file_id,
            "filename": filename,
            "text": sl.text,
            "total_lines": sl.total_lines,
            "returned_range": list(sl.returned_range),
            "truncated": sl.truncated,
        }
