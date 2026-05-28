"""决策路由权限测试."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from forge.agents.hitl import Decision, get_decision_registry, reset_decision_registry
from forge.api.routes.v1.decisions import (
    DecisionSubmitIn,
    get_decision,
    submit_decision,
)
from forge.core.exceptions import Forbidden


@dataclass
class _User:
    user_id: str
    role: str = "member"


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_decision_registry()


@pytest.mark.asyncio
async def test_decision_owner_can_read_and_submit() -> None:
    registry = get_decision_registry()
    pending = registry.create(
        kind="plan",
        payload={"plan_markdown": "x"},
        run_id="run_1",
        owner_user_id="u_owner",
    )

    resp = await get_decision(pending.token, _User(user_id="u_owner"))
    assert resp["code"] == 0
    assert resp["data"]["token"] == pending.token

    submit_resp = await submit_decision(
        pending.token,
        DecisionSubmitIn(approved=True, feedback="ok"),
        _User(user_id="u_owner"),
    )
    assert submit_resp["code"] == 0
    assert submit_resp["data"]["ok"] is True


@pytest.mark.asyncio
async def test_decision_non_owner_forbidden() -> None:
    registry = get_decision_registry()
    pending = registry.create(
        kind="workflow_gate",
        payload={"phase": "review"},
        run_id="run_2",
        owner_user_id="u_owner",
    )

    with pytest.raises(Forbidden):
        await get_decision(pending.token, _User(user_id="u_other"))

    with pytest.raises(Forbidden):
        await submit_decision(
            pending.token,
            DecisionSubmitIn(approved=False, feedback="reject"),
            _User(user_id="u_other"),
        )


@pytest.mark.asyncio
async def test_decision_admin_can_submit() -> None:
    registry = get_decision_registry()
    pending = registry.create(
        kind="plan",
        payload={"plan_markdown": "x"},
        run_id="run_3",
        owner_user_id="u_owner",
    )

    submit_resp = await submit_decision(
        pending.token,
        DecisionSubmitIn(approved=False, feedback="admin reject"),
        _User(user_id="u_admin", role="admin"),
    )
    assert submit_resp["code"] == 0
    assert submit_resp["data"]["ok"] is True
    assert registry.get(pending.token).decided == Decision(
        approved=False,
        feedback="admin reject",
        edited_plan=None,
    )
