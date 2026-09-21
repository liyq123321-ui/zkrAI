"""Admin-only user account management (CRUD, enable/disable, reset password)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_db, require_tenant_admin
from app.auth.roles import (
    ROLE_TENANT_ADMIN,
    default_role_code,
    is_valid_role,
    normalize_role,
    role_codes,
    role_name,
)
from app.database.models import User
from app.security import hash_password

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

VALID_ROLES = set(role_codes())


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default_factory=default_role_code)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


def _serialize(user: User) -> dict:
    # Normalise first so ``role`` and ``role_name`` can never disagree, even for
    # a row the migration has not touched yet.
    role = normalize_role(user.role)
    return {
        "id": user.id,
        "username": user.username,
        "role": role,
        "role_name": role_name(role),
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


def _get_user_or_404(db: Session, username: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": f"用户不存在：{username}"},
        )
    return user


def _active_tenant_admin_count(db: Session) -> int:
    """Active 租户管理员 accounts, tolerating the legacy ``admin`` code."""

    return (
        db.query(User)
        .filter(
            User.role.in_([ROLE_TENANT_ADMIN, "admin"]),
            User.is_active.is_(True),
        )
        .count()
    )


def _resolve_role(raw: str) -> str:
    """Validate and canonicalise an incoming role value.

    Unknown values are rejected outright - never silently downgraded, which
    would hide both typos and attempts to probe the role table.
    """

    if not is_valid_role(raw):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_ROLE",
                "message": "role 必须是 " + " / ".join(role_codes()) + " 之一。",
                "valid_roles": list(role_codes()),
            },
        )
    return normalize_role(raw)


@router.get("")
def list_users(
    _admin: User = Depends(require_tenant_admin), db: Session = Depends(get_db)
) -> list[dict]:
    users = db.query(User).order_by(User.created_at.asc()).all()
    return [_serialize(u) for u in users]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest,
    _admin: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    role = _resolve_role(body.role)
    if role == ROLE_TENANT_ADMIN and _active_tenant_admin_count(db) >= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "TENANT_ADMIN_EXISTS",
                "message": "租户管理员默认唯一，已存在一个活跃的租户管理员。",
            },
        )
    if db.query(User).filter(User.username == body.username).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "USER_EXISTS", "message": f"用户已存在：{body.username}"},
        )
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return _serialize(user)


@router.post("/{username}/disable")
def disable_user(
    username: str,
    admin: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    if username == admin.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SELF_OPERATION", "message": "不能禁用自己的账户。"},
        )
    user = _get_user_or_404(db, username)
    user.is_active = False
    db.commit()
    return _serialize(user)


@router.post("/{username}/enable")
def enable_user(
    username: str,
    _admin: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = _get_user_or_404(db, username)
    if (
        normalize_role(user.role) == ROLE_TENANT_ADMIN
        and not user.is_active
        and _active_tenant_admin_count(db) >= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "TENANT_ADMIN_EXISTS",
                "message": "租户管理员默认唯一，已有活跃的租户管理员。",
            },
        )
    user.is_active = True
    db.commit()
    return _serialize(user)


@router.post("/{username}/reset-password")
def reset_password(
    username: str,
    body: ResetPasswordRequest,
    _admin: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = _get_user_or_404(db, username)
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True, "username": user.username}


@router.delete("/{username}")
def delete_user(
    username: str,
    admin: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> dict:
    if username == admin.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SELF_OPERATION", "message": "不能删除自己的账户。"},
        )
    user = _get_user_or_404(db, username)
    if (
        normalize_role(user.role) == ROLE_TENANT_ADMIN
        and user.is_active
        and _active_tenant_admin_count(db) <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "LAST_ADMIN", "message": "不能删除最后一个活跃的租户管理员。"},
        )
    db.delete(user)
    db.commit()
    return {"ok": True, "username": username}
