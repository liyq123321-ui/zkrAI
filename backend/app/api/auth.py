"""Public authentication endpoints: login, current user, password change."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import auth_secret, auth_token_ttl
from app.auth.deps import get_current_user, get_db
from app.auth.roles import (
    effective_operations,
    normalize_role,
    operation_catalog,
    role_catalog,
    role_name,
)
from app.config import Settings
from app.database.models import User
from app.security import (
    InvalidToken,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


@lru_cache(maxsize=1)
def _settings() -> Settings:
    return Settings.from_env()


def _public_user(user: User) -> dict:
    """The logged-in user shaped for the frontend permission layer."""

    role = normalize_role(user.role)
    allow_business = _settings().rbac_allow_tenant_admin_business
    return {
        "username": user.username,
        "role": role,
        "role_name": role_name(role),
        "operations": list(
            effective_operations(role, allow_tenant_admin_business=allow_business)
        ),
        "is_active": user.is_active,
    }


@router.get("/roles")
def list_roles(_user: User = Depends(get_current_user)) -> dict:
    """The 角色分类 table (定位 / 默认操作点 / 说明) for any signed-in user.

    The frontend uses this to render the classification without hard-coding it,
    and to label the current user's role.
    """

    allow_business = _settings().rbac_allow_tenant_admin_business
    return {
        "roles": role_catalog(),
        "operations": operation_catalog(),
        "allow_tenant_admin_business": allow_business,
    }


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "用户名或密码错误。"},
        )
    token = create_access_token(
        auth_secret(), user.username, normalize_role(user.role), ttl_seconds=auth_token_ttl()
    )
    return LoginResponse(access_token=token, user=_public_user(user))


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return _public_user(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "当前密码不正确。"},
        )
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
