"""部署模式 / 客户端类型工具访问过滤器 (S6.5 M4).

当前产品只实现本地单机 ``deployment_mode=local``: 所有工具调用保持原行为.
``sandbox`` / ``cloud`` 是未来 SaaS 化的策略接入点,本期遇到 dangerous 工具时
显式抛 ``NotImplementedError`` 防止假装已经具备隔离能力.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge.api.middleware.client_type import current_client_type
from forge.infrastructure.storage import get_deployment_mode


@dataclass(frozen=True)
class AccessFilterResult:
    allow: bool
    reason: str = ""


class ToolAccessFilter:
    """ToolExecutor 防护栏管线的最前置控制点."""

    def __init__(
        self,
        *,
        deployment_mode: str | None = None,
        client_type: str | None = None,
    ) -> None:
        self._deployment_mode = deployment_mode
        self._client_type = client_type

    def check(
        self,
        tool: Any,
        role: str,
        args: dict[str, Any] | None = None,  # noqa: ARG002 - 后续策略会用到
    ) -> AccessFilterResult:
        mode = (self._deployment_mode or get_deployment_mode()).strip().lower()
        client_type = (self._client_type or current_client_type()).strip().lower()

        if mode == "local":
            return AccessFilterResult(allow=True)

        if mode in {"sandbox", "cloud"}:
            if getattr(tool, "dangerous", False):
                raise NotImplementedError(
                    f"S6.5 {mode} 策略待实现: client_type={client_type!r}, "
                    f"tool={tool.name!r}, role={role!r}"
                )
            return AccessFilterResult(allow=True)

        raise NotImplementedError(f"deployment_mode={mode!r} 的工具访问策略在 S6.5 阶段未实现")


__all__ = ["AccessFilterResult", "ToolAccessFilter"]
