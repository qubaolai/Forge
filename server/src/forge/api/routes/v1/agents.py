"""/agents 路由 (Agent 管理 CRUD)。"""

from fastapi import APIRouter, Query

from forge.api.dependencies import CurrentUser
from forge.api.schemas.agent import (
    AgentCreateIn,
    AgentUpdateIn,
    orm_to_agent_out,
)
from forge.core.exceptions import Forbidden, NotFound
from forge.core.response import success
from forge.infrastructure.database.orm.agent_orm import AgentOrm
from forge.infrastructure.database.repositories.agent_repo import AgentRepoDep

router = APIRouter(prefix="/agents", tags=["agents"])


def _can_edit(agent: AgentOrm, user_id: str, user_role: str) -> bool:
    if agent.owner_id == user_id:
        return True
    if user_role in ("owner", "admin"):
        return True
    # 协作者 admin 权限
    for c in agent.collaborators or []:
        if isinstance(c, dict) and c.get("user_id") == user_id and c.get("permission") == "admin":
            return True
    return False


def _orm_from_create(body: AgentCreateIn, owner_id: str) -> AgentOrm:
    return AgentOrm(
        name=body.name,
        description=body.description,
        avatar_url=body.avatar_url,
        visibility=body.visibility,
        owner_id=owner_id,
        collaborators=[c.model_dump() for c in body.collaborators],
        mode=body.mode,
        system_prompt=body.system_prompt or "",
        opening_message=body.opening_message,
        model_id=body.model.model_id,
        temperature=body.model.temperature,
        max_tokens=body.model.max_tokens,
        top_p=body.model.top_p,
        kb_ids=body.retrieval.kb_ids,
        retrieval_top_k=body.retrieval.top_k,
        retrieval_score_threshold=body.retrieval.score_threshold,
        retrieval_hybrid=body.retrieval.hybrid,
        retrieval_rerank=body.retrieval.rerank,
        tools=[t.model_dump() for t in body.tools],
        context_window=body.advanced.context_window,
        timeout_seconds=body.advanced.timeout_seconds,
        max_retries=body.advanced.max_retries,
        enable_streaming=body.advanced.enable_streaming,
    )


def _apply_update(agent: AgentOrm, body: AgentUpdateIn) -> None:
    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.avatar_url is not None:
        agent.avatar_url = body.avatar_url
    if body.visibility is not None:
        agent.visibility = body.visibility
    if body.collaborators is not None:
        agent.collaborators = [c.model_dump() for c in body.collaborators]
    if body.mode is not None:
        agent.mode = body.mode
    if body.system_prompt is not None:
        agent.system_prompt = body.system_prompt
    if body.opening_message is not None:
        agent.opening_message = body.opening_message
    if body.model is not None:
        agent.model_id = body.model.model_id
        agent.temperature = body.model.temperature
        agent.max_tokens = body.model.max_tokens
        agent.top_p = body.model.top_p
    if body.retrieval is not None:
        agent.kb_ids = body.retrieval.kb_ids
        agent.retrieval_top_k = body.retrieval.top_k
        agent.retrieval_score_threshold = body.retrieval.score_threshold
        agent.retrieval_hybrid = body.retrieval.hybrid
        agent.retrieval_rerank = body.retrieval.rerank
    if body.tools is not None:
        agent.tools = [t.model_dump() for t in body.tools]
    if body.advanced is not None:
        agent.context_window = body.advanced.context_window
        agent.timeout_seconds = body.advanced.timeout_seconds
        agent.max_retries = body.advanced.max_retries
        agent.enable_streaming = body.advanced.enable_streaming


@router.get("")
async def list_agents(
    user: CurrentUser,
    repo: AgentRepoDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = "",
):
    items, total = await repo.list_accessible(user.id, page, page_size, q)
    return success(
        {
            "items": [orm_to_agent_out(a).model_dump(mode="json") for a in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.post("")
async def create_agent(body: AgentCreateIn, user: CurrentUser, repo: AgentRepoDep):
    agent = _orm_from_create(body, owner_id=user.id)
    await repo.create(agent)
    return success(orm_to_agent_out(agent).model_dump(mode="json"))


@router.get("/{agent_id}")
async def get_agent(agent_id: str, user: CurrentUser, repo: AgentRepoDep):
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise NotFound("Agent 不存在", code=40411)
    return success(orm_to_agent_out(agent).model_dump(mode="json"))


@router.patch("/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdateIn, user: CurrentUser, repo: AgentRepoDep):
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise NotFound("Agent 不存在", code=40411)
    if not _can_edit(agent, user.id, user.role):
        raise Forbidden("无权修改该 Agent", code=40312)
    _apply_update(agent, body)
    await repo.save(agent)
    return success(orm_to_agent_out(agent).model_dump(mode="json"))


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, user: CurrentUser, repo: AgentRepoDep):
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise NotFound("Agent 不存在", code=40411)
    if not _can_edit(agent, user.id, user.role):
        raise Forbidden("无权删除该 Agent", code=40312)
    await repo.delete(agent)
    return success(None)


@router.post("/{agent_id}/duplicate")
async def duplicate_agent(agent_id: str, user: CurrentUser, repo: AgentRepoDep):
    src = await repo.get_by_id(agent_id)
    if not src:
        raise NotFound("Agent 不存在", code=40411)

    copy = AgentOrm(
        name=f"{src.name} 副本",
        description=src.description,
        avatar_url=src.avatar_url,
        visibility="private",
        owner_id=user.id,
        collaborators=[],
        system_prompt=src.system_prompt,
        opening_message=src.opening_message,
        model_id=src.model_id,
        temperature=src.temperature,
        max_tokens=src.max_tokens,
        top_p=src.top_p,
        kb_ids=list(src.kb_ids or []),
        retrieval_top_k=src.retrieval_top_k,
        retrieval_score_threshold=src.retrieval_score_threshold,
        retrieval_hybrid=src.retrieval_hybrid,
        retrieval_rerank=src.retrieval_rerank,
        tools=list(src.tools or []),
        context_window=src.context_window,
        timeout_seconds=src.timeout_seconds,
        max_retries=src.max_retries,
        enable_streaming=src.enable_streaming,
    )
    await repo.create(copy)
    return success(orm_to_agent_out(copy).model_dump(mode="json"))
