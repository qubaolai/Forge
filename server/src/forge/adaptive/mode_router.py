"""ModeRouter: chat | adaptive 双路路由 (B11/P2-11 后简化)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from forge.adaptive.options import TaskOptionsIn

RouteTarget = Literal["chat", "adaptive"]
InputMode = Literal["auto", "chat", "task"]

# B11 之前的关键词路由已废弃：auto 决策只看是否有 task_options.workspace_path
# 保留常量定义供未来"提示性建议"使用（如 UI 提示用户切换 mode=task），
# 不再参与实际 decide 路径


@dataclass(frozen=True)
class RouteDecision:
    """路由决策结果。"""

    target: RouteTarget
    reason: str


class ModeRouter:
    """请求模式路由器。

    设计目标：
    - `mode=chat` 永远走 chat（零退化，TurnOrchestrator）。
    - `mode=task` 永远走 adaptive（显式任务，supervisor 驱动 7 步流程）。
    - `mode=auto` 保守策略：
        * 携带 task_options（含 workspace_path）→ adaptive
        * 否则一律 chat（包括"实现/修复"等任务意图关键词）

    B11/P2-11: 移除中间态 "simple"。adaptive 7 步流程需要 workspace_path 等
    显式上下文，让 auto 自动切换会带来歧义；用户希望"轻量任务"应继续走 chat
    （TurnOrchestrator 内部本就是 ReActAgent），需要工作区编排时显式 mode=task。
    """

    def decide(
        self,
        *,
        mode: InputMode,
        message: str,
        task_options: TaskOptionsIn | None = None,
    ) -> RouteDecision:
        if mode == "chat":
            return RouteDecision(target="chat", reason="显式 mode=chat")
        if mode == "task":
            return RouteDecision(target="adaptive", reason="显式 mode=task")

        # auto: 仅在用户显式传入 task_options（且含 workspace_path）时切到 adaptive。
        if task_options is not None and getattr(task_options, "workspace_path", None):
            return RouteDecision(target="adaptive", reason="auto + task_options.workspace_path")

        return RouteDecision(target="chat", reason="auto 默认走 chat（需任务编排请显式 mode=task）")
