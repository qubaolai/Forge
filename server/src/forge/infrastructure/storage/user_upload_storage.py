"""用户上传文件存储 (UserUploadStorage).

定位:
    - 承载「用户主动上传的附件」, 与 LLM 生成沙盒 (WorkspaceStorage) 彻底分离:
        * WorkspaceStorage: workspace/<user_id>/<session_id>/<relpath> (会话沙盒, 随会话生命周期)
        * UserUploadStorage: user_uploads/<user_id>/<YYYY-MM-DD>/<filename> (与会话解耦)
    - 上传时不绑会话, 落盘只按「用户/日期/文件名」; 会话关系由 DB 的 user_files.session_id 维护。

storage_path 形态:
    相对 user_uploads 根的相对路径 (posix), 形如 "u123/2026-06-16/report.pdf"。
    数据库 user_files.storage_path 只存这个 key, 不存绝对路径, 方便迁移。

安全 (关键):
    - filename 由用户提供, 必须做越界防御: 只取 basename, 禁绝对路径 / ".." / 分隔符。
    - user_id 是系统生成的安全 ID, 作目录名仅做基础校验。
    - 同名冲突自动追加 "-1/-2..." 后缀, 不覆盖已有文件 (上传是新增语义, 非覆盖)。
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from forge.config.domains import paths
from forge.infrastructure.storage.base import StoredFile

logger = logging.getLogger(__name__)


class UserUploadStorage:
    """用户上传文件存储 (本地磁盘)。"""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base = (
            Path(base_dir).resolve() if base_dir else paths.user_uploads_dir().resolve()
        )

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def save(self, *, user_id: str, filename: str, data: bytes) -> StoredFile:
        """落盘到 user_uploads/<user_id>/<date>/<safe_name>, 同名冲突追加后缀。"""
        date_dir = self._safe_date_dir(user_id)
        date_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_filename(filename)
        abs_path = self._dedup_path(date_dir / safe_name)
        abs_path.write_bytes(data)
        rel_to_base = abs_path.relative_to(self._base).as_posix()
        digest = hashlib.sha256(data).hexdigest()
        logger.debug("UserUploadStorage 保存: %s size=%d", rel_to_base, len(data))
        return StoredFile(
            storage_path=rel_to_base,
            size_bytes=len(data),
            content_hash=digest,
        )

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def read(self, storage_path: str) -> bytes:
        return self._resolve_storage_path(storage_path).read_bytes()

    def read_text(self, storage_path: str, encoding: str = "utf-8") -> str:
        return self.read(storage_path).decode(encoding, errors="replace")

    # ------------------------------------------------------------------
    # 删
    # ------------------------------------------------------------------
    def delete(self, storage_path: str) -> bool:
        try:
            abs_path = self._resolve_storage_path(storage_path)
        except ValueError:
            return False
        if not abs_path.exists():
            return False
        abs_path.unlink()
        self._prune_empty_dirs(abs_path.parent)
        return True

    # ------------------------------------------------------------------
    # 内部: 路径解析与越界防御
    # ------------------------------------------------------------------
    def _safe_date_dir(self, user_id: str) -> Path:
        uid = (user_id or "").strip()
        if not uid:
            raise ValueError("user_id 不能为空")
        if any(c in uid for c in ("/", "\\")) or uid == "..":
            raise ValueError(f"非法 user_id: {uid!r}")
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        return (self._base / uid / date).resolve()

    @staticmethod
    def _safe_filename(filename: str) -> str:
        """只取 basename, 去掉路径分量; 空名回落到默认。"""
        name = os.path.basename((filename or "").strip().replace("\\", "/"))
        # 进一步剔除潜在越界片段
        if not name or name in (".", ".."):
            return "attachment.txt"
        return name

    @staticmethod
    def _dedup_path(target: Path) -> Path:
        """同名冲突时追加 -1/-2... 后缀, 直到不存在。"""
        if not target.exists():
            return target
        stem, suffix = target.stem, target.suffix
        i = 1
        while True:
            candidate = target.with_name(f"{stem}-{i}{suffix}")
            if not candidate.exists():
                return candidate
            i += 1

    def _resolve_storage_path(self, storage_path: str) -> Path:
        """把 user_files.storage_path (相对 base 的 key) 解析为绝对路径并校验。"""
        sp = (storage_path or "").strip()
        if not sp or sp.startswith("/") or ".." in sp.split("/"):
            raise ValueError(f"非法 storage_path: {storage_path!r}")
        abs_path = (self._base / sp).resolve()
        if not abs_path.is_relative_to(self._base):
            raise ValueError(f"storage_path 越过 user_uploads 根: {storage_path!r}")
        return abs_path

    def _prune_empty_dirs(self, start: Path) -> None:
        """best-effort 清空空目录链, 失败忽略。"""
        try:
            parent = start
            while (
                parent != self._base
                and parent.is_relative_to(self._base)
                and parent.exists()
                and not any(parent.iterdir())
            ):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            pass


__all__ = ["UserUploadStorage"]
