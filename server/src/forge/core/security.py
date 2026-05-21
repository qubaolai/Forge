"""安全相关: 密码哈希 (bcrypt 直调) + JWT 编解码.

passlib 1.7 (2020 后停更) 依赖 stdlib `crypt`, Python 3.13 已移除, 因此
密码哈希走 bcrypt SDK 直调, 不再通过 CryptContext.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from config.settings import get_settings

cfg = get_settings()

# bcrypt 输入硬上限 72 字节. bcrypt 5.x 超长会 ValueError; passlib 之前是
# 静默截断, 保持等价行为避免老用户登不上 (新用户密码 >72 字节也极罕见).
_BCRYPT_MAX_BYTES = 72


def _truncate(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


# ---------- 密码 ----------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_truncate(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验. 哈希格式错误一律返回 False, 不抛."""
    try:
        return bcrypt.checkpw(_truncate(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------
def _encode(payload: dict, expires_delta: timedelta) -> tuple[str, datetime]:
    expire = datetime.now(UTC) + expires_delta
    payload = {**payload, "exp": expire, "iat": datetime.now(UTC)}
    token = jwt.encode(payload, cfg.app.auth.jwt_secret, algorithm=cfg.app.auth.jwt_algorithm)
    return token, expire


def create_access_token(user_id: str) -> tuple[str, datetime]:
    """返回 (token, 过期时间)。"""
    return _encode(
        {"sub": user_id, "type": "access"},
        timedelta(minutes=cfg.app.auth.access_token_expire_minutes),
    )


def create_refresh_token(user_id: str, jti: str) -> tuple[str, datetime]:
    """refresh token 必须带 jti(JWT ID),用于黑名单。"""
    return _encode(
        {"sub": user_id, "type": "refresh", "jti": jti},
        timedelta(days=cfg.app.auth.refresh_token_expire_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    """解码 JWT;失败抛 PyJWTError 子类异常,由调用方处理。"""
    return jwt.decode(token, cfg.app.auth.jwt_secret, algorithms=[cfg.app.auth.jwt_algorithm])


def get_token_payload(token: str, expected_type: str | None = None) -> dict:
    """解码并校验类型;失败抛 BusinessError。"""
    from forge.core.exceptions import Unauthorized

    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError as e:
        raise Unauthorized("token 已过期", code=40101) from e
    except jwt.PyJWTError as e:
        raise Unauthorized("token 无效", code=40102) from e

    if expected_type and payload.get("type") != expected_type:
        raise Unauthorized("token 类型不匹配", code=40103)
    return payload
