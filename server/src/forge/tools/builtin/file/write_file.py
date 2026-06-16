"""write_file 工具: LLM 把代码写入会话沙盒文件 (工具级实时).

定位 (输出侧友好展示):
    - chat 从「只读」放开为「可写沙盒」的关键工具。LLM 在回复过程中主动调用,
      每次调用即时落盘到该用户该会话的隔离沙盒, 后端据工具结果下发 file_created
      事件给前端渲染文件卡片 (前端不解析工具内容)。
    - 仅用于写代码/文本文件; 解释性说明仍走正文。

安全 (关键):
    - dangerous=True → ToolExecutor 自动写 audit.jsonl。
    - 路径硬校验: 禁绝对路径 / 禁 ".." / resolve 后必须落在
      session_workspace_dir(user_id, session_id) 内 (WorkspaceStorage 内部强制)。
    - 缺 user/session 上下文一律拒绝。
"""

from __future__ import annotations

import mimetypes
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool


@register_tool
class WriteFile(Tool):
    name = "write_file"
    description = (
        "把代码/文本写入当前会话的沙盒文件 (会即时落盘并在前端生成可预览/下载的文件卡片)。"
        "需要产出代码文件时调用本工具: path 为会话内相对路径 (可含子目录, 如 src/main.py), "
        "content 为完整文件内容。同一 path 多次写入会覆盖。解释性说明请仍写在正文, 不要塞进文件。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "会话内相对路径 (禁止绝对路径与 ..), 可含子目录, 如 app.py / src/main.py",
            },
            "content": {
                "type": "string",
                "description": "完整文件内容 (一次性写入)",
            },
        },
        "required": ["path", "content"],
    }
    parallelism_safe = False
    dangerous = True
    audit_payload_fields = ("path",)

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        from forge.core.request_context import (
            current_assistant_message_id,
            current_session_id,
            current_user_id,
        )
        from forge.infrastructure.database.database import session_scope
        from forge.infrastructure.database.repositories.chat_file_repo import (
            ChatFileRepository,
        )
        from forge.infrastructure.storage.workspace_storage import WorkspaceStorage

        path = str(args.get("path") or "").strip()
        content = args.get("content")
        if not path:
            return {"ok": False, "error": "缺少 path"}
        if not isinstance(content, str):
            return {"ok": False, "error": "content 必须是字符串"}

        user_id = current_user_id() or ""
        session_id = current_session_id() or ""
        if not user_id or not session_id:
            return {"ok": False, "error": "缺少用户/会话上下文, 拒绝执行"}

        # 落盘 (WorkspaceStorage 内部做沙盒越界硬校验)
        try:
            stored = WorkspaceStorage().save(
                user_id=user_id,
                session_id=session_id,
                relpath=path,
                data=content.encode("utf-8"),
            )
        except ValueError as e:
            return {"ok": False, "error": f"非法路径: {e}"}
        except OSError as e:
            return {"ok": False, "error": f"写入失败: {e}"}

        mime_type = mimetypes.guess_type(path)[0]
        message_id = current_assistant_message_id() or None

        async with session_scope() as db:
            # 同名复用同一条记录 (不重命名, 同 path 即覆盖), 避免文件列表重复
            meta = await ChatFileRepository(db).upsert_generated(
                owner_user_id=user_id,
                session_id=session_id,
                filename=path,
                storage_path=stored.storage_path,
                size_bytes=stored.size_bytes,
                mime_type=mime_type,
                content_hash=stored.content_hash,
                message_id=message_id,
            )

        return {
            "ok": True,
            "id": meta.id,
            "path": path,
            "filename": path,
            "size_bytes": stored.size_bytes,
            "mime_type": mime_type,
        }
