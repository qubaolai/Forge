"""发起 HTTP 请求的工具.

设计:
    - 用标准库 ``urllib.request`` (零额外依赖, 单机模式不引入 httpx/requests).
    - 默认拒绝 ``localhost`` / 内网 IP (SSRF 防护); 用户显式 ``allow_internal=true``
      可放开.
    - 超时默认 30s, 上限 120s (避免长时间挂住).
    - 响应体截断到 ``max_response_chars`` (默认 100KB).
    - 失败返回友好错误, 不抛异常 (LLM 看错误也能继续推理).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from forge.tools.base import Tool
from forge.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 120.0
_DEFAULT_MAX_RESP_CHARS = 100_000


@register_tool
class HttpRequest(Tool):
    name = "http_request"
    description = (
        "发起 HTTP GET/POST/PUT/DELETE 等请求, 返回 status_code/headers/body. "
        "默认拒绝指向 localhost / 私网 IP, 防止 SSRF; 设置 allow_internal=true 可放开."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "完整 URL (含 scheme), 例如 https://api.example.com/v1/foo",
            },
            "method": {
                "type": "string",
                "description": "HTTP method, 默认 GET",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
            },
            "headers": {
                "type": "object",
                "description": "请求头 key->value",
            },
            "body": {
                "type": "string",
                "description": "请求体 (字符串). JSON 调用方自行 json.dumps 后传入.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": f"超时秒数, 默认 {_DEFAULT_TIMEOUT}, 上限 {_MAX_TIMEOUT}.",
                "minimum": 1,
            },
            "allow_internal": {
                "type": "boolean",
                "description": "是否允许访问 localhost / 私网 IP. 默认 false.",
            },
            "max_response_chars": {
                "type": "integer",
                "description": f"响应体截断长度. 默认 {_DEFAULT_MAX_RESP_CHARS}.",
                "minimum": 100,
            },
        },
        "required": ["url"],
    }
    parallelism_safe = True
    dangerous = True
    audit_payload_fields = ("url", "method")
    timeout_sec = 120.0

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        url: str = args["url"]
        method: str = (args.get("method") or "GET").upper()
        headers: dict[str, str] = args.get("headers") or {}
        body: str | None = args.get("body")
        timeout: float = min(float(args.get("timeout_seconds") or _DEFAULT_TIMEOUT), _MAX_TIMEOUT)
        allow_internal: bool = bool(args.get("allow_internal", False))
        max_chars: int = int(args.get("max_response_chars") or _DEFAULT_MAX_RESP_CHARS)

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"ok": False, "error": f"不支持的 scheme: {parsed.scheme!r}", "url": url}

        if not allow_internal:
            blocked = _check_internal(parsed.hostname)
            if blocked:
                return {
                    "ok": False,
                    "blocked": True,
                    "reason": f"目标地址 {parsed.hostname} 命中 SSRF 防护: {blocked}",
                    "url": url,
                }

        data_bytes: bytes | None = body.encode("utf-8") if body is not None else None
        req = Request(url, data=data_bytes, method=method)
        for k, v in headers.items():
            req.add_header(k, str(v))

        logger.info("HTTP 请求 method=%s url=%s timeout=%.1fs", method, url, timeout)
        try:
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310
                resp_body = resp.read()
                status = resp.status
                resp_headers = dict(resp.headers.items())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"请求失败: {exc}", "url": url, "method": method}

        text = resp_body.decode("utf-8", errors="replace")
        truncated = len(text) > max_chars
        body_preview = text[:max_chars]

        parsed_json: Any | None = None
        ctype = resp_headers.get("Content-Type", "")
        if "json" in ctype.lower():
            try:
                parsed_json = json.loads(text)
            except json.JSONDecodeError:
                parsed_json = None

        return {
            "ok": 200 <= status < 400,
            "url": url,
            "method": method,
            "status_code": status,
            "headers": resp_headers,
            "body": body_preview,
            "body_truncated": truncated,
            "json": parsed_json,
        }


def _check_internal(hostname: str | None) -> str | None:
    """返回非空字符串表示触发了 SSRF 检查."""
    if not hostname:
        return "空 hostname"
    h = hostname.lower()
    if h in ("localhost", "ip6-localhost", "ip6-loopback"):
        return "localhost"
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified:
            return f"非公网 IP ({ip})"
        return None
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        return None
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified:
            return f"解析到非公网 IP ({ip})"
    return None


__all__ = ["HttpRequest", "_check_internal"]
