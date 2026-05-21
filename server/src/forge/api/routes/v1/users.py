"""/users 路由 (管理员用户管理)。"""

import secrets
import string

from fastapi import APIRouter, Query

from forge.api.dependencies import AdminUser
from forge.api.schemas.auth import UserOut
from forge.api.schemas.user import UserCreateIn, UserUpdateIn
from forge.core.exceptions import BadRequest, Conflict, NotFound
from forge.core.response import success
from forge.infrastructure.database.repositories.user_repo import UserRepoDep

router = APIRouter(prefix="/users", tags=["users"])

_VALID_ROLES = {"owner", "admin", "member", "guest"}
_VALID_STATUS = {"active", "disabled", "pending"}


def _generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.get("")
async def list_users(
    admin: AdminUser,
    repo: UserRepoDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = "",
):
    items, total = await repo.list_all(page, page_size, q)
    return success(
        {
            "items": [UserOut.model_validate(u).model_dump(mode="json") for u in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.post("")
async def create_user(body: UserCreateIn, admin: AdminUser, repo: UserRepoDep):
    if body.role not in _VALID_ROLES:
        raise BadRequest(f"role 必须是 {sorted(_VALID_ROLES)} 之一")
    if body.status not in _VALID_STATUS:
        raise BadRequest(f"status 必须是 {sorted(_VALID_STATUS)} 之一")
    existing = await repo.get_by_email(body.email)
    if existing:
        raise Conflict("邮箱已被注册", code=40910)
    user = await repo.create(
        email=body.email,
        password=body.password,
        name=body.name,
        role=body.role,
        avatar_url=body.avatar_url,
        status=body.status,
    )
    return success(UserOut.model_validate(user).model_dump(mode="json"))


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdateIn, admin: AdminUser, repo: UserRepoDep):
    user = await repo.get_by_id(user_id)
    if not user:
        raise NotFound("用户不存在", code=40420)
    if body.name is not None:
        user.name = body.name
    if body.role is not None:
        if body.role not in _VALID_ROLES:
            raise BadRequest(f"role 必须是 {sorted(_VALID_ROLES)} 之一")
        user.role = body.role
    if body.status is not None:
        if body.status not in _VALID_STATUS:
            raise BadRequest(f"status 必须是 {sorted(_VALID_STATUS)} 之一")
        user.status = body.status
    if body.avatar_url is not None:
        user.avatar_url = body.avatar_url
    await repo.save(user)
    return success(UserOut.model_validate(user).model_dump(mode="json"))


@router.delete("/{user_id}")
async def delete_user(user_id: str, admin: AdminUser, repo: UserRepoDep):
    if user_id == admin.id:
        raise BadRequest("不能删除自己", code=40002)
    user = await repo.get_by_id(user_id)
    if not user:
        raise NotFound("用户不存在", code=40420)
    await repo.delete(user)
    return success(None)


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: str, admin: AdminUser, repo: UserRepoDep):
    user = await repo.get_by_id(user_id)
    if not user:
        raise NotFound("用户不存在", code=40420)
    temp = _generate_temp_password()
    await repo.update_password(user, temp)
    return success({"temp_password": temp})
