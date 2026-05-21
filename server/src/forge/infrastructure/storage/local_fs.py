"""本地磁盘 FileStorage 实现.

目录结构:
    {base_dir}/{kb_id}/{document_id}/{filename}

为什么用 (kb_id, document_id) 两级:
    - 单 KB 删除时 rm -rf 一个目录即可
    - 同名文件多次上传走不同 document_id 不冲突
    - 目录名都是 ASCII 安全 ID, 不需要文件名编码处理

storage_path 形态:
    相对于 base_dir 的相对路径, 形如 "kb_xxx/doc_xxx/handbook.pdf".
    数据库只存这个 key, 不存 base_dir, 方便整体迁移 / 容器化部署.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from forge.infrastructure.storage.base import FileStorage, StoredFile

logger = logging.getLogger(__name__)

# 文件名安全字符: 字母/数字/中文/常见符号, 避免目录穿越
_UNSAFE_CHAR = re.compile(r"[\\/\x00-\x1f]")


def _sanitize_filename(name: str) -> str:
    """剥掉路径分隔与控制字符, 保留扩展名. 空名兜底为 'unnamed'."""
    cleaned = _UNSAFE_CHAR.sub("_", name).strip()
    return cleaned or "unnamed"


class LocalFileStorage(FileStorage):
    """本地磁盘实现.

    配置:
        base_dir: 根目录绝对路径. 不存在会自动创建.
    """

    def __init__(self, base_dir: str | Path):
        self._base = Path(base_dir).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        logger.info("LocalFileStorage 就绪: base_dir=%s", self._base)

    def save(
        self,
        *,
        kb_id: str,
        document_id: str,
        filename: str,
        data: bytes,
    ) -> StoredFile:
        safe_name = _sanitize_filename(filename)
        rel_path = f"{kb_id}/{document_id}/{safe_name}"
        abs_path = self._base / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        # symlink/二级穿越防御: 确保最终 absolute path 仍在 base_dir 下
        if not str(abs_path.resolve()).startswith(str(self._base) + "/"):
            raise ValueError(f"非法 storage_path (越过 base_dir): {rel_path}")

        abs_path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        logger.debug("LocalFileStorage 保存: %s size=%d", rel_path, len(data))
        return StoredFile(
            storage_path=rel_path,
            size_bytes=len(data),
            content_hash=digest,
        )

    def open(self, storage_path: str) -> bytes:
        abs_path = self._resolve(storage_path)
        return abs_path.read_bytes()

    def delete(self, storage_path: str) -> bool:
        try:
            abs_path = self._resolve(storage_path)
        except ValueError:
            return False
        if not abs_path.exists():
            return False
        abs_path.unlink()
        # 顺便清空空目录链 (best-effort, 失败忽略)
        try:
            parent = abs_path.parent
            while parent != self._base and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            pass
        return True

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _resolve(self, storage_path: str) -> Path:
        """把相对 key 解析为绝对路径, 并校验仍在 base_dir 下."""
        if storage_path.startswith("/") or ".." in storage_path.split("/"):
            raise ValueError(f"非法 storage_path: {storage_path!r}")
        abs_path = (self._base / storage_path).resolve()
        if not str(abs_path).startswith(str(self._base) + "/"):
            raise ValueError(f"非法 storage_path (越过 base_dir): {storage_path!r}")
        return abs_path
