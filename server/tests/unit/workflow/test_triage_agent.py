from __future__ import annotations

import pytest

from forge.agents.triage import TriageAgent


@pytest.mark.asyncio
async def test_triage_agent_decide():
    agent = TriageAgent.create_default()
    decision = await agent.decide("请帮我跑一下回归测试")
    assert decision.template_id == "regression_test"


@pytest.mark.asyncio
async def test_triage_agent_empty_message_defaults_question_only():
    agent = TriageAgent.create_default()
    decision = await agent.decide("")
    assert decision.template_id == "question_only"
