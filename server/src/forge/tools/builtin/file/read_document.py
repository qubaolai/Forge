"""read_document 工具: 读取会话中上传的 Word/Excel/PDF 等文档为 Markdown.

与 read_file 的分工:
    - read_file:     纯文本/代码文件, 直接按行返回 (支持 line_range 切片)。
    - read_document: 二进制文档 (docx/xlsx/pdf 等), 用解析器转成 Markdown 再返回。
      Excel 按工作表 (sheet) 组织, 可选 sheet 只取某一页。

鉴权与 read_file 一致: 读 current_user_id, 仅允许读取归属该用户的会话文件。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from forge.tools.base import Tool
from forge.tools.registry import register_tool

from ._access import resolve_owned_file

logger = logging.getLogger(__name__)

# 解析后 Markdown 的返回上限 (对话文档通常很小; 兜底防极端文件撑爆上下文)
_MAX_MARKDOWN_CHARS = 60_000


@register_tool
class ReadDocument(Tool):
    name = "read_document"
    description = (
        "读取会话中上传的文档 (Word/Excel/PDF 等) 并解析为 Markdown 文本。"
        "当用户上传的 [file:<id>] 是 .docx/.xlsx/.pdf 等非纯文本文档、需要基于其内容回答时调用。"
        "Excel 按工作表 (sheet) 组织, 返回 sheets 列表; 可选 sheet 参数只取某一页, 避免整表过长。"
        "纯文本 / 代码文件请改用 read_file。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "文档 ID, 即引用占位 [file:<id>] 中的 id",
            },
            "sheet": {
                "type": "string",
                "description": "(可选, 仅 Excel) 只读取该工作表名; 不传则返回全部工作表",
            },
        },
        "required": ["file_id"],
    }
    parallelism_safe = True

    async def arun(self, args: dict[str, Any]) -> dict[str, Any]:
        from forge.core.request_context import current_user_id

        file_id = str(args.get("file_id") or "").strip()
        if not file_id:
            return {"ok": False, "error": "缺少 file_id"}
        sheet = str(args.get("sheet") or "").strip() or None

        user_id = current_user_id() or ""
        if not user_id:
            return {"ok": False, "error": "缺少用户上下文, 拒绝执行"}

        resolved = await resolve_owned_file(file_id, user_id)
        if resolved is None:
            return {"ok": False, "error": f"文件未找到或无权访问: {file_id}"}
        filename, storage, storage_path = resolved

        try:
            data = storage.read(storage_path)
        except (FileNotFoundError, ValueError, OSError):
            return {"ok": False, "error": f"文件内容读取失败: {file_id}"}

        try:
            rendered, fmt = await asyncio.to_thread(
                _parse_to_markdown, filename, data, sheet
            )
        except _UnsupportedFormat as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:  # noqa: BLE001
            logger.exception("read_document 解析失败: %s", filename)
            return {"ok": False, "error": f"文档解析失败: {filename}"}

        markdown = rendered.markdown
        truncated = len(markdown) > _MAX_MARKDOWN_CHARS
        if truncated:
            markdown = markdown[:_MAX_MARKDOWN_CHARS].rstrip() + "\n\n...(文档过长已截断)"

        result: dict[str, Any] = {
            "ok": True,
            "file_id": file_id,
            "filename": filename,
            "format": fmt,
            "markdown": markdown,
            "truncated": truncated,
        }
        if rendered.sheet_names:
            result["sheets"] = rendered.sheet_names
        return result


class _UnsupportedFormat(Exception):
    """无对应解析器 (缺依赖或格式不支持)."""


def _parse_to_markdown(filename: str, data: bytes, sheet: str | None):
    """把上传文件字节解析成 Markdown (同步, 跑在线程里)."""
    from forge.retrieval.document_render import elements_to_markdown
    from forge.retrieval.parsers.dispatcher import default_dispatcher

    suffix = Path(filename).suffix.lower()
    dispatcher = default_dispatcher()

    tmpdir = Path(tempfile.mkdtemp(prefix="readdoc_"))
    tmp_path = tmpdir / (Path(filename).name or f"upload{suffix}")
    try:
        tmp_path.write_bytes(data)
        parser = dispatcher.get(tmp_path)
        if parser is None:
            raise _UnsupportedFormat(
                f"暂不支持读取该格式: {suffix or '未知'} "
                f"(已支持: {', '.join(dispatcher.supported_extensions())})"
            )
        elements = parser.parse(tmp_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not elements:
        raise _UnsupportedFormat(f"文档解析为空: {filename}")
    return elements_to_markdown(elements, sheet_filter=sheet), suffix.lstrip(".")
