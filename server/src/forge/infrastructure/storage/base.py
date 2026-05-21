"""文件存储协议.

设计原则:
    - 流式接口: save 接受 bytes (V1 足够), V2 加 save_stream 支持大文件
    - storage_path 是后端无关的"键" (local fs 是相对路径, S3 是 key);
      数据库只存这个 key, 不存绝对路径, 方便迁移
    - 不在协议里强求并发安全, 由具体实现声明
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredFile:
    """保存后返回的引用.

    storage_path: 给数据库存的"键", 配合 FileStorage 后端解析回真实位置.
    size_bytes:   实际写入字节数 (与上传方传入大小校验对齐).
    content_hash: SHA256 hex, 供去重和完整性校验.
    """

    storage_path: str
    size_bytes: int
    content_hash: str


class FileStorage(ABC):
    """文件存储抽象."""

    @abstractmethod
    def save(
        self,
        *,
        kb_id: str,
        document_id: str,
        filename: str,
        data: bytes,
    ) -> StoredFile:
        """保存文件. 同 (kb_id, document_id, filename) 多次写入应覆盖.

        Args:
            kb_id:       所属 KB ID, 用于实现层组织目录 / 对象前缀
            document_id: 文档 ID
            filename:    原始文件名 (含扩展名); 实现层可基于它选保存策略
            data:        文件二进制内容

        Returns:
            StoredFile 引用. storage_path 写到 kb_documents.storage_path.
        """

    @abstractmethod
    def open(self, storage_path: str) -> bytes:
        """按 storage_path 读取文件全部内容. V1 一次性读入内存, 大文件场景 V2 再换流式."""

    @abstractmethod
    def delete(self, storage_path: str) -> bool:
        """删除单个文件. 返回是否真删除 (不存在时返回 False, 不抛)."""
