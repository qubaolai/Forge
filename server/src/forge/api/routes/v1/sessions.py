"""/sessions 路由。"""

from fastapi import APIRouter, Query

from forge.api.dependencies import AuthenticatedUser
from forge.api.schemas.chat import MessageOut, SessionCreateIn, SessionUpdateIn
from forge.api.services.session_service import SessionServiceDep
from forge.core.response import success

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _derive_context_usage(context_meta: dict | None) -> dict | None:
    """从落库的 context_meta 派生前端可渲染的上下文占用 (含分层)。

    仅 assistant 消息的 context_meta 含 estimated_input_tokens / context_window,
    其余消息返回 None。
    """
    cm = context_meta or {}
    tok = cm.get("estimated_input_tokens")
    cw = cm.get("context_window")
    if not tok or not cw:
        return None
    return {
        "input_tokens": tok,
        "context_window": cw,
        "total_ratio": cm.get("total_ratio") or (tok / cw),
        "layers": cm.get("layers") or [],
    }


@router.get("")
async def list_sessions(
    user: AuthenticatedUser,
    svc: SessionServiceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = "",
):
    items, total = await svc.list_for_user(user.user_id, page, page_size, q)
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.post("")
async def create_session(body: SessionCreateIn, user: AuthenticatedUser, svc: SessionServiceDep):
    session = await svc.create(user.user_id, title=body.title)
    return success(await svc.get_single_enriched(session))


@router.get("/{session_id}")
async def get_session(session_id: str, user: AuthenticatedUser, svc: SessionServiceDep):
    session = await svc.get_owned(session_id, user.user_id)
    return success(await svc.get_single_enriched(session))


@router.patch("/{session_id}")
async def update_session(
    session_id: str,
    body: SessionUpdateIn,
    user: AuthenticatedUser,
    svc: SessionServiceDep,
):
    session = await svc.get_owned(session_id, user.user_id)
    session = await svc.rename(session, body.title)
    return success(await svc.get_single_enriched(session))


@router.delete("/{session_id}")
async def delete_session(session_id: str, user: AuthenticatedUser, svc: SessionServiceDep):
    session = await svc.get_owned(session_id, user.user_id)
    await svc.delete(session)
    return success(None)


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: str,
    user: AuthenticatedUser,
    svc: SessionServiceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    await svc.get_owned(session_id, user.user_id)  # 校验归属
    items, total = await svc.list_messages(session_id, page, page_size)
    items_out = []
    for i in items:
        d = MessageOut.model_validate(i).model_dump(mode="json")
        d["context_usage"] = _derive_context_usage(getattr(i, "context_meta", None))
        items_out.append(d)
    return success(
        {
            "items": items_out,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )
