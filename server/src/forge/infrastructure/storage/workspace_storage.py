"""会话沙盒文件存储 (WorkspaceStorage).

定位:
    - 为 chat 会话提供「用户隔离 + 会话隔离」的可写沙盒, 承载:
        * 用户上传的大段输入附件 (source=upload)
        * LLM 通过 write_file 工具产出的代码文件 (source=generated)
    - 与 KB 的 LocalFileStorage 分开: KB 是 (kb_id, document_id, filename) 三级,
      沙盒是 workspace/<user_id>/<session_id>/<relpath>, relpath 允许子目录。

storage_path 形态:
    相对 workspace 根的相对路径 (posix), 形如 "u123/s456/src/main.py"。
    数据库 chat_files.storage_path 只存这个 key, 不存绝对路径, 方便迁移。

安全 (关键):
    - relpath 由调用方 (含 LLM 的 write_file) 提供, 必须做越界防御:
        禁绝对路径 / 禁 ".." / resolve 后必须仍在该会话目录内。
    - user_id / session_id 是系统生成的安全 ID, 作目录名仅做基础校验。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from forge.config.domains import paths
from forge.infrastructure.storage.base import StoredFile

logger = logging.getLogger(__name__)


class WorkspaceStorage:
    """会话沙盒存储 (本地磁盘)。"""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base = Path(base_dir).resolve() if base_dir else paths.workspace_dir().resolve()

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def save(
        self, *, user_id: str, session_id: str, relpath: str, data: bytes
    ) -> StoredFile:
        """落盘到 workspace/<user_id>/<session_id>/<relpath>, 覆盖同名。"""
        _session_root, abs_path = self._resolve(user_id, session_id, relpath)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(data)
        rel_to_base = abs_path.relative_to(self._base).as_posix()
        digest = hashlib.sha256(data).hexdigest()
        logger.debug("WorkspaceStorage 保存: %s size=%d", rel_to_base, len(data))
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

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """删除整个会话沙盒目录 (会话删除时级联调用)。"""
        try:
            session_root = self._safe_session_root(user_id, session_id)
        except ValueError:
            return False
        if not session_root.exists():
            return False
        shutil.rmtree(session_root, ignore_errors=True)
        self._prune_empty_dirs(session_root.parent)
        return True

    # ------------------------------------------------------------------
    # 内部: 路径解析与越界防御
    # ------------------------------------------------------------------
    def _safe_session_root(self, user_id: str, session_id: str) -> Path:
        uid = (user_id or "").strip()
        sid = (session_id or "").strip()
        if not uid or not sid:
            raise ValueError("user_id / session_id 不能为空")
        if any(c in uid or c in sid for c in ("/", "\\")) or ".." in (uid, sid):
            raise ValueError(f"非法 user_id/session_id: {uid!r}/{sid!r}")
        return (self._base / uid / sid).resolve()

    def _resolve(
        self, user_id: str, session_id: str, relpath: str
    ) -> tuple[Path, Path]:
        """解析 (会话根, 目标绝对路径), 并校验目标仍在会话根内。"""
        session_root = self._safe_session_root(user_id, session_id)
        rp = (relpath or "").strip()
        # 禁绝对路径 / 反斜杠 / ..: 绝对路径不静默中和, 直接拒绝 (符合工具「禁止绝对路径」语义)
        if not rp or rp.startswith(("/", "\\")) or "\\" in rp or ".." in Path(rp).parts:
            raise ValueError(f"非法 relpath: {relpath!r}")
        abs_path = (session_root / rp).resolve()
        if not abs_path.is_relative_to(session_root):
            raise ValueError(f"relpath 越过会话沙盒: {relpath!r}")
        return session_root, abs_path

    def _resolve_storage_path(self, storage_path: str) -> Path:
        """把 chat_files.storage_path (相对 base 的 key) 解析为绝对路径并校验。"""
        sp = (storage_path or "").strip()
        if not sp or sp.startswith("/") or ".." in sp.split("/"):
            raise ValueError(f"非法 storage_path: {storage_path!r}")
        abs_path = (self._base / sp).resolve()
        if not abs_path.is_relative_to(self._base):
            raise ValueError(f"storage_path 越过 workspace 根: {storage_path!r}")
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


__all__ = ["WorkspaceStorage"]
