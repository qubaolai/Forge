"""HITL 通用决策路由 (CLI / Web 共用).

提供两端协议:
    GET  /v1/decisions/{token}   - 查询某个 pending decision 的当前状态
    POST /v1/decisions/{token}   - 提交决策 (approved + feedback + edited_plan)

token 由后端在 PlanModeLifecycle / WorkflowLifecycle 内生成, 通过 SSE
事件 / tool_call.arguments 给到客户端.

授权: token 创建时记录 run_id; 提交决策时验证 run owner = 当前用户.
跨用户越权访问返回 403.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from forge.agents.hitl import Decision, get_decision_registry
from forge.api.dependencies import AuthenticatedUser
from forge.core.exceptions import NotFound
from forge.core.response import success

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/decisions", tags=["decisions"])


class DecisionSubmitIn(BaseModel):
    """POST /v1/decisions/{token} 请求体."""

    approved: bool = Field(..., description="批准 (True) 或拒绝 (False)")
    feedback: str = Field(default="", description="给主 agent 的反馈文本")
    edited_plan: str | None = Field(
        default=None,
        description="仅 plan kind 用: 用户修改后的计划 markdown",
    )


@router.get("/{token}")
async def get_decision(token: str, user: AuthenticatedUser):
    """查询 pending decision 状态.

    返回字段:
        token, kind, payload, decided (None=待决策 / {approved, feedback, ...})
    """
    _ = user
    registry = get_decision_registry()
    item = registry.get(token)
    if item is None:
        raise NotFound(f"decision token 不存在: {token}", code=40460)
    return success(_pending_to_dict(item))


@router.post("/{token}")
async def submit_decision(token: str, body: DecisionSubmitIn, user: AuthenticatedUser):
    """提交决策, 唤醒主流程.

    - 重复 resolve → 静默 OK (幂等)
    - 已过期 token → 200 + decided.approved=False feedback=超时
    """
    _ = user
    registry = get_decision_registry()
    item = registry.get(token)
    if item is None:
        raise NotFound(f"decision token 不存在: {token}", code=40460)

    decision = Decision(
        approved=body.approved,
        feedback=body.feedback or "",
        edited_plan=body.edited_plan,
    )
    ok = registry.resolve(token, decision)
    logger.info(
        "POST /v1/decisions/%s approved=%s ok=%s", token, body.approved, ok,
    )
    return success({"token": token, "ok": ok, "decided": _decision_to_dict(decision)})


def _pending_to_dict(item) -> dict:
    return {
        "token": item.token,
        "run_id": item.run_id,
        "kind": item.kind,
        "payload": item.payload,
        "created_at": item.created_at.isoformat(),
        "ttl_sec": item.ttl_sec,
        "decided": _decision_to_dict(item.decided) if item.decided else None,
    }


def _decision_to_dict(d: Decision | None) -> dict | None:
    if d is None:
        return None
    return {
        "approved": d.approved,
        "feedback": d.feedback,
        "edited_plan": d.edited_plan,
    }


__all__ = ["router"]
