"""ModeRouter: chat | adaptive 双路路由。"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from forge.adaptive.options import TaskOptionsIn

RouteTarget = Literal["chat", "adaptive"]
InputMode = Literal["auto", "chat", "task"]
ModeClassifier = Callable[[str, TaskOptionsIn], RouteTarget | Awaitable[RouteTarget]]

logger = logging.getLogger(__name__)


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
    - `mode=auto` 只在存在 workspace_path 时启用 LLM 判别；否则走 chat。
    """

    def __init__(self, classifier: ModeClassifier | None = None) -> None:
        self._classifier = classifier

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

        if task_options is not None and getattr(task_options, "workspace_path", None):
            return RouteDecision(target="chat", reason="auto + workspace_path 等待 LLM 判别")

        return RouteDecision(target="chat", reason="auto 默认走 chat（未提供 workspace_path）")

    async def adecide(
        self,
        *,
        mode: InputMode,
        message: str,
        task_options: TaskOptionsIn | None = None,
    ) -> RouteDecision:
        """异步决策入口；auto + workspace_path 时使用 LLM 判别。"""
        base = self.decide(mode=mode, message=message, task_options=task_options)
        if mode != "auto" or task_options is None or not getattr(task_options, "workspace_path", None):
            return base

        try:
            target = await self._classify(message, task_options)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ModeRouter LLM 判别失败，回退 chat")
            return RouteDecision(target="chat", reason=f"auto LLM 判别失败，回退 chat: {exc}")

        return RouteDecision(target=target, reason=f"auto LLM 判别: {target}")

    async def _classify(self, message: str, task_options: TaskOptionsIn) -> RouteTarget:
        if self._classifier is not None:
            result = self._classifier(message, task_options)
            if inspect.isawaitable(result):
                result = await result
            if result not in {"chat", "adaptive"}:
                raise ValueError(f"非法 classifier 结果: {result!r}")
            return result

        return await _classify_with_llm(message)


async def _classify_with_llm(message: str) -> RouteTarget:
    """用轻量 LLM 判别本轮是否需要 adaptive 任务编排。"""
    import asyncio

    from config.settings import get_settings

    from forge.llm.gateway import build_chain_from_settings
    from forge.llm.providers.base import ChatMessage

    settings = get_settings()
    target_model = _resolve_router_model(settings)
    chain = build_chain_from_settings(settings, model=target_model)
    messages = [
        ChatMessage(
            role="system",
            content=(
                "你是 Forge 的路由判别器，只输出 JSON。\n"
                "判断用户本轮输入是否需要进入 adaptive 任务处理流程。\n"
                "adaptive 适用于：需要修改代码/文件、执行多步骤工程任务、运行测试验证、"
                "生成补丁或跨文件实现。\n"
                "chat 适用于：解释、问答、讨论方案、阅读理解、普通咨询，"
                "即使用户提供了工作空间目录也不应自动进入 adaptive。\n"
                '输出格式固定为 {"target":"chat"} 或 {"target":"adaptive"}。'
            ),
        ),
        ChatMessage(role="user", content=message),
    ]

    result = await asyncio.to_thread(
        chain.chat,
        messages,
        temperature=0.0,
        max_tokens=80,
    )
    return _parse_classifier_output(result.content)


def _resolve_router_model(settings) -> str | None:
    task_cfg = getattr(settings, "task_execution", None)
    profile = str(getattr(task_cfg, "mode_router_model_profile", "fast"))
    profiles = getattr(task_cfg, "model_profiles", None)
    if profiles is None:
        return None
    if isinstance(profiles, dict):
        value = profiles.get(profile)
    else:
        value = getattr(profiles, profile, None)
    return str(value) if value else None


def _parse_classifier_output(text: str) -> RouteTarget:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("LLM 判别结果为空")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise ValueError(f"LLM 判别结果不是 JSON: {raw[:120]}") from None
        payload = json.loads(match.group(0))

    target = str(payload.get("target", "")).strip().lower()
    if target == "task":
        target = "adaptive"
    if target not in {"chat", "adaptive"}:
        raise ValueError(f"LLM 判别 target 非法: {target!r}")
    return target  # type: ignore[return-value]
