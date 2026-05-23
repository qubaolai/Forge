"""工具访问过滤器 — 当前全部放行。sandbox / cloud 策略为未来扩展点。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AccessFilterResult:
    allow: bool
    reason: str = ""


class ToolAccessFilter:
    """ToolExecutor 防护栏管线的最前置控制点。"""

    def __init__(self, *, client_type: str | None = None) -> None:
        self._client_type = client_type

    def check(
        self,
        tool: Any,
        role: str,
        args: dict[str, Any] | None = None,
    ) -> AccessFilterResult:
        return AccessFilterResult(allow=True)


__all__ = ["AccessFilterResult", "ToolAccessFilter"]
