"""危险操作拦截防护栏.

职责
----
工具调用前的最后一道闸:
    - shell 命令: 复用 ``tools.builtin.code.shell._is_blocked`` 的黑名单 (rm -rf /,
      sudo, curl|sh, fork bomb 等)
    - write_file: 拒绝写入危险路径 (``/etc``, ``/usr``, ``/bin``, ``/System`` 等
      系统目录)
    - http_request: 拦截明显的可疑端点 (``file://``, ``ftp://`` 之类已经在工具
      内部拒了, 这里补一道 SSRF 之外的关键字 dnslog / interactsh)

输出
----
``check(tool_name, args) -> BlockResult``:
    - allow=True   -> 放行
    - allow=False  -> 含 reason, 调用方拒绝执行 + 写 audit
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.tools.builtin.code.shell import _is_blocked as _shell_blocked

logger = logging.getLogger(__name__)


_SYSTEM_PATH_PREFIXES: tuple[str, ...] = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/System",
    "/Library/LaunchDaemons",
    "/Library/StartupItems",
    "/var/log",
    "/dev",
    "/proc",
    "/sys",
)

_HTTP_SUSPECT_HOST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.dnslog\.cn$", re.IGNORECASE),
    re.compile(r"\.interactsh\.com$", re.IGNORECASE),
    re.compile(r"\.burpcollaborator\.net$", re.IGNORECASE),
)


@dataclass(frozen=True)
class BlockResult:
    allow: bool
    reason: str = ""


class DangerousOpBlocker:
    """跨工具的危险操作拦截器."""

    def check(self, tool_name: str, args: dict[str, Any] | None) -> BlockResult:
        args = args or {}
        if tool_name == "shell":
            return self._check_shell(args)
        if tool_name == "write_file":
            return self._check_write_file(args)
        if tool_name == "http_request":
            return self._check_http(args)
        return BlockResult(allow=True)

    @staticmethod
    def _check_shell(args: dict[str, Any]) -> BlockResult:
        cmd = args.get("command")
        if not isinstance(cmd, str):
            return BlockResult(allow=True)
        matched = _shell_blocked(cmd)
        if matched:
            return BlockResult(
                allow=False,
                reason=f"shell 命令命中危险模式: {matched}",
            )
        return BlockResult(allow=True)

    @staticmethod
    def _check_write_file(args: dict[str, Any]) -> BlockResult:
        path_str = args.get("path")
        if not isinstance(path_str, str) or not path_str.strip():
            return BlockResult(allow=True)
        p = Path(path_str).expanduser()
        # 用 absolute() 而非 resolve(): macOS 上 /etc 是 /private/etc 软链, resolve
        # 会跳过 /etc 前缀检查. 我们要拦的是"用户的意图", 而不是实际物理路径.
        candidate = p if p.is_absolute() else (Path.cwd() / p)
        try:
            absolute_str = str(candidate.absolute())
        except OSError:
            return BlockResult(allow=True)
        for prefix in _SYSTEM_PATH_PREFIXES:
            if absolute_str == prefix or absolute_str.startswith(prefix + "/"):
                return BlockResult(
                    allow=False,
                    reason=f"禁止写入系统目录: {prefix}",
                )
        return BlockResult(allow=True)

    @staticmethod
    def _check_http(args: dict[str, Any]) -> BlockResult:
        from urllib.parse import urlparse

        url = args.get("url")
        if not isinstance(url, str):
            return BlockResult(allow=True)
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return BlockResult(allow=True)
        for pat in _HTTP_SUSPECT_HOST_PATTERNS:
            if pat.search(host):
                return BlockResult(
                    allow=False,
                    reason=f"目标 host 命中可疑模式: {pat.pattern}",
                )
        return BlockResult(allow=True)


__all__ = ["BlockResult", "DangerousOpBlocker"]
