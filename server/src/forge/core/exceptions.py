"""自定义业务异常 + 全局异常处理器。

业务错误码遵循规范:5 位数字,前 3 位 = HTTP 状态码,后 2 位 = 细分序号。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from forge.core.response import fail_response

logger = logging.getLogger(__name__)


class BusinessError(Exception):
    """业务异常基类。

    code: 5 位业务码;http_status: 对应 HTTP 状态码;message: 用户可读信息。
    """

    def __init__(
        self,
        code: int,
        message: str,
        http_status: int = 400,
        details: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details
        super().__init__(message)


# ---- 常用快捷异常 ----
class BadRequest(BusinessError):
    def __init__(self, message: str = "参数错误", code: int = 40000, details=None):
        super().__init__(code, message, 400, details)


class Unauthorized(BusinessError):
    def __init__(self, message: str = "未认证", code: int = 40100):
        super().__init__(code, message, 401)


class Forbidden(BusinessError):
    def __init__(self, message: str = "权限不足", code: int = 40300):
        super().__init__(code, message, 403)


class NotFound(BusinessError):
    def __init__(self, message: str = "资源不存在", code: int = 40400):
        super().__init__(code, message, 404)


class Conflict(BusinessError):
    def __init__(self, message: str = "资源冲突", code: int = 40900):
        super().__init__(code, message, 409)


class TooManyRequests(BusinessError):
    def __init__(self, message: str = "请求过于频繁", code: int = 42900):
        super().__init__(code, message, 429)


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器,保证所有错误响应都符合统一格式。"""

    @app.exception_handler(BusinessError)
    async def _business_handler(request: Request, exc: BusinessError):
        return fail_response(exc.code, exc.message, exc.http_status, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        # pydantic v2 在 field_validator 抛 ValueError 时, errors() 返回的
        # ctx 里会带原始 ValueError 实例, 直接 JSONResponse 会序列化失败.
        # 这里统一把 ctx 折叠成字符串, 让响应永远可序列化.
        cleaned: list[dict] = []
        for e in exc.errors():
            item = dict(e)
            ctx = item.get("ctx")
            if isinstance(ctx, dict):
                item["ctx"] = {k: str(v) for k, v in ctx.items()}
            cleaned.append(item)
        return fail_response(
            40001,
            "参数校验失败",
            422,
            details={"errors": cleaned},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException):
        # 把 fastapi 内置的 HTTPException 也包装成统一格式
        code = int(f"{exc.status_code}00") if exc.status_code < 1000 else 50000
        return fail_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception):
        logger.exception("未捕获的异常: %s", exc)
        return fail_response(50000, "服务器内部错误", 500)
