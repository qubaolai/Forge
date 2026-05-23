"""AES-256-GCM 对称加密 — 用于 model_providers.api_key 可逆存储。

密钥从环境变量 FORGE_ENCRYPTION_KEY 读取（Base64 编码的 32 字节）。
注意: 密钥在首次调用 encrypt/decrypt 时延迟读取（非 import 时），
因为 .env 文件由 settings.py 在 get_settings() 时加载，晚于模块 import。
"""

from __future__ import annotations

import base64
import os
import secrets

from forge.core.exceptions import BusinessError

_KEY_BYTES: bytes | None = None
_KEY_LOADED: bool = False


def _load_key() -> bytes | None:
    """延迟加载加密密钥。仅在首次调用时读取环境变量。"""
    global _KEY_BYTES, _KEY_LOADED
    if _KEY_LOADED:
        return _KEY_BYTES
    _KEY_LOADED = True

    raw = os.environ.get("FORGE_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw)
        if len(decoded) != 32:
            raise ValueError("FORGE_ENCRYPTION_KEY 解码后必须为 32 字节")
        _KEY_BYTES = decoded
    except Exception:
        _KEY_BYTES = None
    return _KEY_BYTES


def _ensure_key() -> bytes:
    """获取加密密钥，未配置则抛错。"""
    key = _load_key()
    if key is None:
        raise BusinessError(
            code=50000,
            message="FORGE_ENCRYPTION_KEY 未配置或无效，无法加解密 API Key",
            http_status=500,
        )
    return key


def is_encryption_available() -> bool:
    """检查加密密钥是否已配置。"""
    return _load_key() is not None


def encrypt(plaintext: str) -> str:
    """AES-256-GCM 加密，返回 Base64 编码的密文 (nonce + ciphertext + tag)。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _ensure_key()
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(encoded: str) -> str:
    """AES-256-GCM 解密，返回明文。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _ensure_key()
    raw = base64.b64decode(encoded)
    nonce = raw[:12]
    ciphertext = raw[12:]
    aesgcm = AESGCM(key)
    plain = aesgcm.decrypt(nonce, ciphertext, None)
    return plain.decode("utf-8")


def resolve_key(key_ciphertext: str) -> str:
    """将 DB 中存储的 key 转为明文。

    FORGE_ENCRYPTION_KEY 已配置时走 AES 解密；解密失败则视作明文原样返回。
    未配置时直接返回原值（明文存储模式）。
    """
    if not key_ciphertext:
        return ""
    if not is_encryption_available():
        return key_ciphertext
    try:
        return decrypt(key_ciphertext)
    except Exception:
        return key_ciphertext
