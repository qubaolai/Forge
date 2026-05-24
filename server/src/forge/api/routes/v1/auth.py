"""/auth 路由。"""

from typing import Annotated

from forge.config.settings import get_settings
from fastapi import APIRouter, Cookie, Response

from forge.api.dependencies import AuthenticatedUser
from forge.api.schemas.auth import ChangePasswordIn, LoginIn, LoginOut, RefreshOut, UserOut
from forge.api.services.auth_service import AuthServiceDep
from forge.core.exceptions import Unauthorized
from forge.core.response import success
from forge.core.security import verify_password
from forge.infrastructure.database.repositories.user_repo import UserRepoDep

router = APIRouter()

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"

settings = get_settings()


def _set_refresh_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.app.auth.cookie_secure,
        samesite=settings.app.auth.cookie_samesite,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.app.auth.cookie_secure,
        samesite=settings.app.auth.cookie_samesite,
    )


@router.post("/login")
async def login(body: LoginIn, response: Response, svc: AuthServiceDep):
    user = await svc.authenticate(body.email, body.password)
    access, access_exp, refresh, _refresh_exp, _jti = svc.issue_tokens(user.user_id)

    max_age = settings.app.auth.refresh_token_expire_days * 24 * 3600
    _set_refresh_cookie(response, refresh, max_age)

    data = LoginOut(
        access_token=access,
        expires_at=access_exp,
        user=UserOut.model_validate(user),
    )
    return success(data.model_dump(mode="json"))


@router.post("/logout")
async def logout(
    response: Response,
    svc: AuthServiceDep,
    refresh_token: Annotated[str | None, Cookie()] = None,
):
    await svc.revoke(refresh_token)
    _clear_refresh_cookie(response)
    return success(None)


@router.post("/refresh")
async def refresh(
    svc: AuthServiceDep,
    refresh_token: Annotated[str | None, Cookie()] = None,
):
    access, access_exp, _user = await svc.refresh_access(refresh_token)
    data = RefreshOut(access_token=access, expires_at=access_exp)
    return success(data.model_dump(mode="json"))


@router.get("/me")
async def me(user: AuthenticatedUser):
    return success(UserOut.model_validate(user).model_dump(mode="json"))


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn,
    user: AuthenticatedUser,
    user_repo: UserRepoDep,
):
    if not verify_password(body.old_password, user.password_hash):
        raise Unauthorized("原密码错误", code=40112)
    if body.old_password == body.new_password:
        raise Unauthorized("新密码不能与原密码相同", code=40113)
    await user_repo.update_password(user, body.new_password)
    return success(None)
