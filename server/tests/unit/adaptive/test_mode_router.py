"""ModeRouter 路由规则测试。"""

from __future__ import annotations

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


def test_auto_with_task_options_routes_adaptive() -> None:
    router = ModeRouter()
    decision = router.decide(
        mode="auto",
        message="随便",
        task_options=TaskOptionsIn(workspace_path="."),
    )
    assert decision.target == "adaptive"


def test_auto_question_routes_chat() -> None:
    router = ModeRouter()
    decision = router.decide(mode="auto", message="这个接口为什么会超时？")
    assert decision.target == "chat"


def test_auto_task_intent_without_options_routes_chat() -> None:
    """B11/P2-11: 移除 simple 路径后，auto + 任务关键词但未传 task_options 一律 chat。"""
    router = ModeRouter()
    decision = router.decide(mode="auto", message="请实现一个新的用户导出脚本")
    assert decision.target == "chat"
    assert "mode=task" in decision.reason
