"""请求限流中间件 (Redis 固定窗口实现).

策略: 按 user_id (有 token) 或 client_ip 维度, 每分钟桶计数.
失败时返回 429, 在响应头给出 Retry-After / X-RateLimit-* 提示.

为何用固定窗口而非令牌桶:
    固定窗口实现简单, 一次 INCR+EXPIRE 搞定, 边界尖峰可接受.
    后续要平滑限流, 换 sliding-window-log 或 Lua 令牌桶, 接口不变.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# 不限流的路径前缀
_EXEMPT_PREFIXES = ("/api/v1/system/health", "/api/v1/system/ready", "/docs", "/openapi.json")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        redis_getter: Callable[[], Redis],
        per_minute: int,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self._redis_getter = redis_getter
        self._per_minute = per_minute
        self._enabled = enabled

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled or request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        key = self._make_key(request)
        try:
            allowed, remaining, reset_in = await self._check(key)
        except Exception as e:  # noqa: BLE001
            # Redis 挂了不能阻塞业务: fail-open + 告警
            logger.warning("rate_limit 检查失败 (fail-open): %s", e)
            return await call_next(request)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "code": 42900,
                    "message": "请求过于频繁, 请稍后再试",
                    "retry_after_seconds": reset_in,
                },
                headers={
                    "Retry-After": str(reset_in),
                    "X-RateLimit-Limit": str(self._per_minute),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + reset_in),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self._per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _make_key(self, request: Request) -> str:
        """限流维度: 优先 user_id, 否则 client_ip.

        user_id 由后续 auth 中间件挂到 request.state, 这里能拿到就用.
        """
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return f"rl:user:{user_id}"
        ip = request.client.host if request.client else "anon"
        return f"rl:ip:{ip}"

    async def _check(self, key: str) -> tuple[bool, int, int]:
        """返回 (是否放行, 剩余配额, 当前窗口剩余秒数)."""
        redis = self._redis_getter()
        bucket_window = int(time.time() // 60)  # 当前分钟
        bucket_key = f"{key}:{bucket_window}"

        pipe = redis.pipeline()
        pipe.incr(bucket_key)
        pipe.expire(bucket_key, 65)  # 留 5 秒缓冲
        count, _ = await pipe.execute()

        remaining = max(0, self._per_minute - count)
        reset_in = 60 - int(time.time()) % 60
        return count <= self._per_minute, remaining, reset_in
