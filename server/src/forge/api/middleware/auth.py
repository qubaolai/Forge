"""Auth 解析中间件 (软认证).

职责: 解析 Authorization header 中的 JWT, 把 user_id 挂到 request.state.user_id.
不强制拒绝, 强制拒绝在 dependency 层 (CurrentUser) 完成.

为何分两层:
    - 中间件: 通用的请求上下文丰富 (rate_limit / logging / tracing 用 user_id)
    - dependency: 路由级精确控制 (公开接口不需要 user)

注意: 这里不做 DB 查询 (避免每个请求都打 DB), 只解 JWT 拿 sub.
完整用户对象由 dependency 走缓存 + DB 获取.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from forge.core.security import get_token_payload

logger = logging.getLogger(__name__)


class AuthContextMiddleware(BaseHTTPMiddleware):
    """解析 JWT 并把 user_id 挂到 request.state, 失败静默."""

    async def dispatch(self, request: Request, call_next) -> Response:
        authz = request.headers.get("Authorization")
        if authz:
            parts = authz.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                try:
                    payload = get_token_payload(parts[1], expected_type="access")
                    request.state.user_id = payload.get("sub")
                except Exception:
                    # 中间件层不拒绝; 路由 dependency 会再校验一次并明确拒绝
                    request.state.user_id = None

        return await call_next(request)
