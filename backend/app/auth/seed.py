"""Idempotent seeding of the built-in accounts into the users table."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.auth.roles import (
    ROLE_PROJECT_MANAGER,
    ROLE_TENANT_ADMIN,
    normalize_role,
)
from app.database.models import User
from app.security import hash_password

# Built-in accounts and their RBAC roles.
#
# * ``admin``    -> 租户管理员 (tenant_admin): manages accounts/roles/security.
# * ``owner-1``  -> 项目经理 (project_manager): owns all pre-existing content,
#                   which is attributed to this account.
#
# The remaining roles (一般员工 / 专家 / 只读) are intentionally NOT assigned to a
# built-in account yet - see docs/RBAC_ROLES.md.
DEFAULT_USERS = (
    {"username": "owner-1", "password": "password123", "role": ROLE_PROJECT_MANAGER},
    {"username": "admin", "password": "admin123", "role": ROLE_TENANT_ADMIN},
)


def seed_default_users(session_factory: Callable[[], Session]) -> None:
    """Create the built-in accounts if they do not already exist.

    Existing accounts are left untouched, except that a legacy role value is
    normalised onto its canonical code (``owner`` -> ``project_manager``).
    """

    with session_factory() as db:
        existing = {row.username: row for row in db.query(User).all()}
        created = False
        changed = False
        for spec in DEFAULT_USERS:
            current = existing.get(spec["username"])
            if current is None:
                db.add(
                    User(
                        username=spec["username"],
                        password_hash=hash_password(spec["password"]),
                        role=spec["role"],
                        is_active=True,
                    )
                )
                created = True
                continue
            canonical = normalize_role(current.role)
            if current.role != canonical:
                current.role = canonical
                changed = True
        if created or changed:
            db.commit()
