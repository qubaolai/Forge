"""统一响应封装。

规范要求所有 JSON 响应(非 SSE)采用:
    { "code": 0, "data": ..., "message": "ok", "details": {...}? }
"""

from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ResponseModel(BaseModel):
    code: int = 0
    data: Any = None
    message: str = "ok"
    details: dict | None = None


def success(data: Any = None, message: str = "ok") -> dict:
    """构造成功响应体(直接返回 dict,FastAPI 会序列化)。"""
    return {"code": 0, "data": data, "message": message}


def fail_response(
    code: int,
    message: str,
    http_status: int = 400,
    details: dict | None = None,
) -> JSONResponse:
    """构造失败响应,HTTP 状态码与业务 code 解耦。"""
    body: dict = {"code": code, "data": None, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=http_status, content=body)
