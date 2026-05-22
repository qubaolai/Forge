"""ModeRouter 路由规则测试。"""

from __future__ import annotations

import pytest

from forge.adaptive.mode_router import ModeRouter
from forge.adaptive.options import TaskOptionsIn


def test_explicit_chat_mode_routes_chat() -> None:
    router = ModeRouter()
    decision = router.decide(mode="chat", message="请帮我解释这段代码")
    assert decision.target == "chat"


def test_explicit_task_mode_routes_adaptive() -> None:
    router = ModeRouter()
    decision = router.decide(mode="task", message="修复登录 bug")
    assert decision.target == "adaptive"


def test_auto_with_task_options_sync_fallback_routes_chat() -> None:
    router = ModeRouter()
    decision = router.decide(
        mode="auto",
        message="随便",
        task_options=TaskOptionsIn(workspace_path="."),
    )
    assert decision.target == "chat"
    assert "LLM 判别" in decision.reason


async def _adaptive_classifier(message: str, task_options: TaskOptionsIn) -> str:
    return "adaptive"


async def _chat_classifier(message: str, task_options: TaskOptionsIn) -> str:
    return "chat"


@pytest.mark.asyncio
async def test_auto_with_workspace_can_route_adaptive_by_classifier() -> None:
    router = ModeRouter(classifier=_adaptive_classifier)  # type: ignore[arg-type]
    decision = await router.adecide(
        mode="auto",
        message="请修复登录 bug 并运行测试",
        task_options=TaskOptionsIn(workspace_path="."),
    )
    assert decision.target == "adaptive"


@pytest.mark.asyncio
async def test_auto_with_workspace_can_stay_chat_by_classifier() -> None:
    router = ModeRouter(classifier=_chat_classifier)  # type: ignore[arg-type]
    decision = await router.adecide(
        mode="auto",
        message="解释一下这个目录结构",
        task_options=TaskOptionsIn(workspace_path="."),
    )
    assert decision.target == "chat"


def test_auto_question_routes_chat() -> None:
    router = ModeRouter()
    decision = router.decide(mode="auto", message="这个接口为什么会超时？")
    assert decision.target == "chat"


def test_auto_task_intent_without_options_routes_chat() -> None:
    """B11/P2-11: 移除 simple 路径后，auto + 任务关键词但未传 task_options 一律 chat。"""
    router = ModeRouter()
    decision = router.decide(mode="auto", message="请实现一个新的用户导出脚本")
    assert decision.target == "chat"
    assert "workspace_path" in decision.reason
