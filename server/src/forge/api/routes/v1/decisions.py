"""HITL 通用决策路由 (CLI / Web 共用).

提供两端协议:
    GET  /v1/decisions/{token}   - 查询某个 pending decision 的当前状态
    POST /v1/decisions/{token}   - 提交决策 (approved + feedback + edited_plan)

token 由后端在 PlanModeLifecycle / WorkflowLifecycle 内生成, 通过 SSE
事件给到客户端。

授权: token 创建时记录 run_id + owner_user_id; 提交决策时验证 owner.
跨用户越权访问返回 403.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from forge.agents.hitl import Decision, get_decision_registry
from forge.agents.run_supervisor import get_run_supervisor
from forge.api.dependencies import AuthenticatedUser
from forge.core.exceptions import Forbidden, NotFound
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
    registry = get_decision_registry()
    item = registry.get(token)
    if item is None:
        raise NotFound(f"decision token 不存在: {token}", code=40460)
    _ensure_decision_owner(item, user)
    return success(_pending_to_dict(item))


@router.post("/{token}")
async def submit_decision(token: str, body: DecisionSubmitIn, user: AuthenticatedUser):
    """提交决策, 唤醒主流程.

    - 重复 resolve → 静默 OK (幂等)
    - 已过期 token → 200 + decided.approved=False feedback=超时
    """
    registry = get_decision_registry()
    item = registry.get(token)
    if item is None:
        raise NotFound(f"decision token 不存在: {token}", code=40460)
    _ensure_decision_owner(item, user)

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


def _ensure_decision_owner(item, user) -> None:
    """校验当前用户是否可读取/提交该 decision."""
    if getattr(user, "role", "") in ("owner", "admin"):
        return

    # 优先用 registry 中显式记录的 owner 校验
    owner_user_id = getattr(item, "owner_user_id", None)
    if owner_user_id:
        if owner_user_id == user.user_id:
            return
        raise Forbidden("无权访问该 decision", code=40331)

    # 兼容历史 token: 退化到 run_supervisor 内存信息校验
    run_id = getattr(item, "run_id", None)
    if run_id:
        orch = get_run_supervisor().get(run_id)
        if orch is not None and orch.record.owner_user_id == user.user_id:
            return

    # 无法证明归属时默认拒绝，避免越权
    raise Forbidden("无权访问该 decision", code=40331)


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
