"""统一 chat / workflow 入口的单测 — skipped: workflow_dispatcher replaced by ModeRouter.

覆盖点:
1. workflow 字段为 None → resolve_workflow_route 返回 None (走 chat).
2. workflow.template_id 显式 → 直接返回该模板 id, 不调 triage.
3. workflow.mode=auto + 命中非对话级模板 → 返回 triage 决定的模板 id.
4. workflow.mode=auto + 命中 question_only → 返回 None (降级 chat).
5. schema 层 template_id 非法值 422 拒绝.
6. schema 层 template_id 与 mode 互斥校验.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.skip(reason="workflow_dispatcher deprecated, replaced by ModeRouter")
from pydantic import ValidationError

from forge.api.schemas.chat import ChatCompletionIn, ChatWorkflowOption
from forge.chat.workflow_dispatcher import (
    CHAT_FALLBACK_TEMPLATES,
    resolve_workflow_route,
)


@dataclass
class _Decision:
    template_id: str
    intent: str = "auto"
    reason: str = "test"


class _Orchestrator:
    def __init__(self, decision: _Decision) -> None:
        self._decision = decision
        self.triage_called = 0

    async def triage(self, message: str) -> _Decision:  # noqa: ARG002
        self.triage_called += 1
        return self._decision


class _Service:
    def __init__(self, decision: _Decision) -> None:
        self.orchestrator = _Orchestrator(decision)


@pytest.mark.asyncio
async def test_resolve_no_workflow_field_returns_none() -> None:
    svc = _Service(_Decision(template_id="quick_fix"))
    result = await resolve_workflow_route(
        workflow_option=None,
        service=svc,
        message="任意消息",
    )
    assert result is None
    assert svc.orchestrator.triage_called == 0


@pytest.mark.asyncio
async def test_resolve_explicit_template_skips_triage() -> None:
    svc = _Service(_Decision(template_id="should_not_be_used"))
    result = await resolve_workflow_route(
        workflow_option=ChatWorkflowOption(template_id="quick_fix"),
        service=svc,
        message="任意消息",
    )
    assert result == "quick_fix"
    # 显式 template_id 必须跳过 triage, 不走规则分类.
    assert svc.orchestrator.triage_called == 0


@pytest.mark.asyncio
async def test_resolve_auto_picks_workflow_template() -> None:
    svc = _Service(_Decision(template_id="quick_fix"))
    result = await resolve_workflow_route(
        workflow_option=ChatWorkflowOption(mode="auto"),
        service=svc,
        message="修一个 bug",
    )
    assert result == "quick_fix"
    assert svc.orchestrator.triage_called == 1


@pytest.mark.asyncio
async def test_resolve_auto_falls_back_to_chat_for_question_only() -> None:
    # question_only 是 CHAT_FALLBACK_TEMPLATES 的成员, 应降级走 chat.
    assert "question_only" in CHAT_FALLBACK_TEMPLATES
    svc = _Service(_Decision(template_id="question_only"))
    result = await resolve_workflow_route(
        workflow_option=ChatWorkflowOption(mode="auto"),
        service=svc,
        message="什么是 ReAct",
    )
    assert result is None
    assert svc.orchestrator.triage_called == 1


def test_schema_rejects_unknown_template_id() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ChatCompletionIn(message="hi", workflow={"template_id": "no_such_template"})
    msg = str(excinfo.value)
    assert "未知 workflow 模板" in msg


def test_schema_rejects_template_and_mode_together() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ChatCompletionIn(
            message="hi",
            workflow={"template_id": "quick_fix", "mode": "auto"},
        )
    assert "不能同时设置" in str(excinfo.value)


def test_schema_accepts_no_workflow_field() -> None:
    """向后兼容: 不传 workflow 字段时模型校验通过."""
    body = ChatCompletionIn(message="hi")
    assert body.workflow is None
