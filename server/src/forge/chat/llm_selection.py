"""把 Agent 的 model_id 解析成 LLMFallbackChain.

抽出来是为了:
    - TurnOrchestrator / Runner 不直接调 build_chain_from_settings
    - 单测 Runner 时可注入 fake chain
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.llm.gateway import build_chain_from_settings

if TYPE_CHECKING:
    from forge.infrastructure.database.orm.agent_orm import AgentOrm


def build_llm_chain_for_agent(agent: AgentOrm | None, settings):
    """根据 agent.model_id 解析 (provider, model) 然后构链.

    agent.model_id 形如:
        - "provider:model"  -> 切到具体 provider+model
        - "model"           -> 走默认 provider, 指定 model
        - None / ""         -> 全用默认
    """
    provider = None
    model = None
    if agent and agent.model_id:
        if ":" in agent.model_id:
            provider, model = agent.model_id.split(":", 1)
        else:
            model = agent.model_id
    return build_chain_from_settings(settings, provider=provider, model=model)
