"""AES-256-GCM 对称加密 — 用于 model_providers.api_key 可逆存储。

密钥从环境变量 FORGE_ENCRYPTION_KEY 读取（Base64 编码的 32 字节）。
"""

from __future__ import annotations

import base64
import os
import secrets

from forge.core.exceptions import BusinessError

_KEY = os.environ.get("FORGE_ENCRYPTION_KEY", "").strip()
_KEY_BYTES: bytes | None = None

if _KEY:
    try:
        decoded = base64.b64decode(_KEY)
        if len(decoded) != 32:
            raise ValueError("FORGE_ENCRYPTION_KEY 解码后必须为 32 字节")
        _KEY_BYTES = decoded
    except Exception:
        _KEY_BYTES = None


def _ensure_key() -> bytes:
    """获取加密密钥，未配置则抛错。"""
    if _KEY_BYTES is None:
        raise BusinessError(
            code=50000,
            message="FORGE_ENCRYPTION_KEY 未配置或无效，无法加解密 API Key",
            http_status=500,
        )
    return _KEY_BYTES


def encrypt(plaintext: str) -> str:
    """AES-256-GCM 加密，返回 Base64 编码的密文 (nonce + ciphertext + tag)。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _ensure_key()
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # nonce(12) + ciphertext(含 16 字节 tag)
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
