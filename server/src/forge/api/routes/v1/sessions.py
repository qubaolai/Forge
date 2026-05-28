"""/sessions 路由。"""

from fastapi import APIRouter, Query

from forge.api.dependencies import AuthenticatedUser
from forge.api.schemas.chat import MessageOut, SessionCreateIn, SessionUpdateIn
from forge.api.services.session_service import SessionServiceDep
from forge.core.response import success

router = APIRouter(prefix="/sessions", tags=["sessions"])


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
    return success(
        {
            "items": [MessageOut.model_validate(i).model_dump(mode="json") for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )
