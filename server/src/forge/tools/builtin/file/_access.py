"""会话文件访问共享逻辑: 按 file_id 解析归属当前用户的上传文件.

read_file / read_document 共用, 保证鉴权与来源解析口径一致:
先查用户上传 (user_files), 未命中再查生成沙盒 (chat_files); 越权当作未找到。
"""

from __future__ import annotations

from typing import Any


async def resolve_owned_file(file_id: str, user_id: str) -> tuple[str, Any, str] | None:
    """返回 (filename, storage, storage_path); 找不到或非本人所有返回 None."""
    from forge.infrastructure.database.database import get_session_factory
    from forge.infrastructure.database.repositories.chat_file_repo import (
        ChatFileRepository,
    )
    from forge.infrastructure.database.repositories.user_file_repo import (
        UserFileRepository,
    )
    from forge.infrastructure.storage.user_upload_storage import UserUploadStorage
    from forge.infrastructure.storage.workspace_storage import WorkspaceStorage

    factory = get_session_factory()
    async with factory() as db:
        uf = await UserFileRepository(db).get_by_id(file_id)
        if uf is not None and uf.owner_user_id == user_id:
            return uf.filename, UserUploadStorage(), uf.storage_path
        cf = await ChatFileRepository(db).get_by_id(file_id)
        if cf is not None and cf.owner_user_id == user_id:
            return cf.filename, WorkspaceStorage(), cf.storage_path
    return None
