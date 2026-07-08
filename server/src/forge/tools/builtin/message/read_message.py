"""read_message 工具: 按 message_id 回读历史消息原文 (支持 line_range 切片).

配合 digest 引用化: 当历史里某条长消息被折叠为 [ref:msg:<id>] + digest 占位时,
LLM 需要原文 (某个函数 / 某段) 就调本工具按 line_range 定向回读, 避免整段拉回再次爆窗。
回读结果以 role="tool" 进上下文, 受 ToolResultPolicy 管, 用完下一轮换出 (paging 自洽)。
"""

from __future__ import annotations

from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

_DEFAULT_PREVIEW_LINES = 300


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
class ReadMessage(Tool):
    name = "read_message"
    description = (
        "按 message_id 回读历史消息原文。当历史中某条消息被折叠为 [ref:msg:<id>] 引用占位时, "
        "用本工具读取其完整内容; 可选 line_range=[起始行,结束行] (1-based 闭区间) 只取片段, "
        "避免整段拉回。不传 line_range 时只返回开头预览页, 如 truncated=true 请继续分段读取。"
        "返回字段: text / total_lines / returned_range / truncated。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "消息 ID, 即引用占位 [ref:msg:<id>] 中的 id",
            },
            "line_range": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "(可选) 1-based 闭区间 [起始行, 结束行]; 不传则返回开头预览页",
            },
        },
        "required": ["message_id"],
    }
    parallelism_safe = True

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        from forge.core.request_context import current_user_id
        from forge.infrastructure.storage.content_store import DbMessageContentStore

        message_id = str(args.get("message_id") or "").strip()
        if not message_id:
            return {"ok": False, "error": "缺少 message_id"}

        # 鉴权: 与 knowledge_search 一致, 读 current_user_id ContextVar;
        # 缺用户上下文一律拒绝, 并把回读限定在该用户自己的会话消息 (防越权读取)。
        user_id = current_user_id() or ""
        if not user_id:
            return {"ok": False, "error": "缺少用户上下文, 拒绝执行"}

        requested_range = _parse_line_range(args.get("line_range"))
        line_range = requested_range or (1, _DEFAULT_PREVIEW_LINES)
        store = DbMessageContentStore()
        sl = await store.get(f"msg:{message_id}", line_range, owner_user_id=user_id)
        if sl is None:
            return {"ok": False, "error": f"消息未找到或无权访问: {message_id}"}
        return {
            "ok": True,
            "message_id": message_id,
            "text": sl.text,
            "total_lines": sl.total_lines,
            "returned_range": list(sl.returned_range),
            "truncated": sl.truncated,
            "range_required": requested_range is None and sl.truncated,
            "hint": (
                "输出为预览页; 如需后续内容, 继续调用 read_message 并传入 line_range"
                if requested_range is None and sl.truncated
                else ""
            ),
        }
