"""FastAPI dependencies that resolve and authorize the authenticated user."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.auth import auth_secret, auth_token_ttl
from app.auth.roles import has_operation, is_tenant_admin, normalize_role
from app.database.database import SessionLocal
from app.database.models import User
from app.security import InvalidToken, decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Return the active user for a valid Bearer token, else 401."""

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "缺少登录凭证。"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(auth_secret(), credentials.credentials)
    except InvalidToken:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "登录凭证无效或已过期。"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = (
        db.query(User)
        .filter(User.username == claims["sub"], User.is_active.is_(True))
        .first()
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_DISABLED", "message": "该用户已被禁用。"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_tenant_admin(user: User = Depends(get_current_user)) -> User:
    """Restrict an endpoint to 租户管理员 (``tenant_admin``).

    Legacy ``admin`` rows are accepted via :func:`normalize_role`.
    """

    if not is_tenant_admin(user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "需要租户管理员权限。"},
        )
    return user


# Backwards-compatible alias: existing routers import ``require_admin``.
require_admin = require_tenant_admin


def require_operation(operation: str) -> Callable[..., User]:
    """Build a dependency that requires one RBAC operation point.

    Usage::

        @router.post("/graph/nodes", dependencies=[Depends(require_operation("project.graph.create"))])
    """

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if not has_operation(user.role, operation):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": "当前角色缺少所需操作点。",
                    "operation": operation,
                    "role": normalize_role(user.role),
                },
            )
        return user

    return _dependency
