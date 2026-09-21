"""Integration tests for the RBAC role classification on the auth/admin APIs.

Exercises the real routers (``/api/auth/*`` and ``/api/admin/users``) against an
in-memory database, so the seeded accounts, the login payload, the operation
points and the 租户管理员 uniqueness rules are all verified end to end.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.roles import ROLE_PROJECT_MANAGER, ROLE_TENANT_ADMIN
from app.auth.seed import seed_default_users
from app.config import Settings
from app.database.models import User
from app.security import hash_password
from main import create_app
from tests.helpers.fake_agent import ScriptedAgentGateway


@pytest.fixture
def rbac_client(session_factory, tmp_path):
    """A real app with the built-in accounts seeded."""

    seed_default_users(session_factory)
    settings = Settings(
        database_url="sqlite://",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        codex_cwd=tmp_path,
        codex_timeout_seconds=1,
        auth_jwt_secret="rbac-test-secret",
    )
    app = create_app(
        settings, ScriptedAgentGateway(analyze_results=deque()), session_factory
    )
    with TestClient(app) as value:
        yield value


def _token(client: TestClient, username: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin_headers(rbac_client):
    return _token(rbac_client, "admin", "admin123")


@pytest.fixture
def manager_headers(rbac_client):
    return _token(rbac_client, "owner-1", "password123")


def test_seeded_accounts_hold_the_expected_roles(session_factory):
    seed_default_users(session_factory)
    with session_factory() as db:
        users = {u.username: u.role for u in db.query(User).all()}
    # 当前分配：owner-1 -> 项目经理，admin -> 租户管理员。
    assert users == {
        "owner-1": ROLE_PROJECT_MANAGER,
        "admin": ROLE_TENANT_ADMIN,
    }


def test_seeding_migrates_legacy_role_values(session_factory):
    """A pre-RBAC row must be normalised, not left as ``owner``."""

    with session_factory() as db:
        db.add(
            User(
                username="owner-1",
                password_hash=hash_password("password123"),
                role="owner",
                is_active=True,
            )
        )
        db.add(
            User(
                username="admin",
                password_hash=hash_password("admin123"),
                role="admin",
                is_active=True,
            )
        )
        db.commit()

    seed_default_users(session_factory)

    with session_factory() as db:
        users = {u.username: u.role for u in db.query(User).all()}
    assert users == {
        "owner-1": ROLE_PROJECT_MANAGER,
        "admin": ROLE_TENANT_ADMIN,
    }


def test_login_payload_carries_the_classification(rbac_client):
    response = rbac_client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    user = response.json()["user"]
    assert user["role"] == ROLE_TENANT_ADMIN
    assert user["role_name"] == "租户管理员"
    assert "admin.user.manage" in user["operations"]
    assert "admin.security.manage" in user["operations"]
    # 租户管理员 默认不做具体业务操作。
    assert "project.graph.edit" not in user["operations"]
    assert user["is_active"] is True


def test_project_manager_login_payload(rbac_client):
    response = rbac_client.post(
        "/api/auth/login", json={"username": "owner-1", "password": "password123"}
    )
    user = response.json()["user"]
    assert user["role"] == ROLE_PROJECT_MANAGER
    assert user["role_name"] == "项目经理"
    assert set(user["operations"]) == {
        "project.graph.create",
        "project.graph.edit",
        "project.graph.assign",
        "project.review.confirm",
        "project.board.flow",
        "project.checklist.config",
        "project.report.export",
    }
    assert "admin.user.manage" not in user["operations"]


def test_me_reflects_the_stored_role(rbac_client, manager_headers):
    body = rbac_client.get("/api/auth/me", headers=manager_headers).json()
    assert body["role"] == ROLE_PROJECT_MANAGER
    assert body["role_name"] == "项目经理"


def test_roles_catalogue_endpoint(rbac_client, admin_headers):
    response = rbac_client.get("/api/auth/roles", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()

    assert [role["code"] for role in body["roles"]] == [
        "tenant_admin",
        "project_manager",
        "employee",
        "expert",
        "viewer",
    ]
    assert body["roles"][0]["name"] == "租户管理员"
    assert body["roles"][0]["positioning"] == "租户内系统管理员，不做具体业务操作"
    assert body["roles"][0]["unique"] is True
    assert body["roles"][2]["default_member"] is True
    assert body["allow_tenant_admin_business"] is False
    # Every role reports human-readable operation names for the UI table.
    assert "图与节点 · 分派" in body["roles"][1]["operation_names"]
    assert len(body["operations"]) == 23


def test_roles_catalogue_requires_authentication(rbac_client):
    assert rbac_client.get("/api/auth/roles").status_code == 401


def test_non_admin_cannot_use_the_user_admin_api(rbac_client, manager_headers):
    # 项目经理 manages projects, not accounts.
    assert rbac_client.get("/api/admin/users", headers=manager_headers).status_code == 403
    create = rbac_client.post(
        "/api/admin/users",
        headers=manager_headers,
        json={"username": "x", "password": "password123", "role": "employee"},
    )
    assert create.status_code == 403


def test_list_users_exposes_role_and_role_name(rbac_client, admin_headers):
    users = rbac_client.get("/api/admin/users", headers=admin_headers).json()
    by_name = {u["username"]: u for u in users}
    assert by_name["admin"]["role_name"] == "租户管理员"
    assert by_name["owner-1"]["role_name"] == "项目经理"


@pytest.mark.parametrize("bad_role", ["root", "superuser", "administrator", ""])
def test_unknown_roles_are_rejected(rbac_client, admin_headers, bad_role):
    response = rbac_client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "probe-1", "password": "password123", "role": bad_role},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_ROLE"
    assert response.json()["detail"]["valid_roles"] == [
        "tenant_admin",
        "project_manager",
        "employee",
        "expert",
        "viewer",
    ]


def test_legacy_role_input_is_normalised_not_rejected(rbac_client, admin_headers):
    response = rbac_client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "legacy-1", "password": "password123", "role": "owner"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == ROLE_PROJECT_MANAGER
    assert response.json()["role_name"] == "项目经理"


def test_default_role_is_the_default_member_role(rbac_client, admin_headers):
    response = rbac_client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "no-role-1", "password": "password123"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "employee"
    assert response.json()["role_name"] == "一般员工"


def test_each_specialist_role_can_be_created(rbac_client, admin_headers):
    for username, code, label in (
        ("expert-1", "expert", "专家"),
        ("viewer-1", "viewer", "只读"),
        ("pm-2", "project_manager", "项目经理"),
    ):
        response = rbac_client.post(
            "/api/admin/users",
            headers=admin_headers,
            json={"username": username, "password": "password123", "role": code},
        )
        assert response.status_code == 201, response.text
        assert response.json()["role"] == code
        assert response.json()["role_name"] == label


def test_tenant_admin_is_unique_by_default(rbac_client, admin_headers):
    response = rbac_client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "admin-2", "password": "password123", "role": "tenant_admin"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "TENANT_ADMIN_EXISTS"


def test_a_second_tenant_admin_cannot_be_enabled(rbac_client, admin_headers, session_factory):
    with session_factory() as db:
        db.add(
            User(
                username="admin-dormant",
                password_hash=hash_password("password123"),
                role=ROLE_TENANT_ADMIN,
                is_active=False,
            )
        )
        db.commit()

    response = rbac_client.post(
        "/api/admin/users/admin-dormant/enable", headers=admin_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "TENANT_ADMIN_EXISTS"


def test_enabling_a_non_admin_role_always_succeeds(rbac_client, admin_headers, session_factory):
    with session_factory() as db:
        db.add(
            User(
                username="expert-dormant",
                password_hash=hash_password("password123"),
                role="expert",
                is_active=False,
            )
        )
        db.commit()

    response = rbac_client.post(
        "/api/admin/users/expert-dormant/enable", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is True
    assert response.json()["role_name"] == "专家"


def test_tenant_admin_cannot_disable_or_delete_itself(rbac_client, admin_headers):
    assert (
        rbac_client.post("/api/admin/users/admin/disable", headers=admin_headers).status_code
        == 400
    )
    assert (
        rbac_client.delete("/api/admin/users/admin", headers=admin_headers).status_code
        == 400
    )


def test_unauthenticated_requests_are_rejected(rbac_client):
    assert rbac_client.get("/api/admin/users").status_code == 401
    assert rbac_client.post(
        "/api/admin/users", json={"username": "z", "password": "password123"}
    ).status_code == 401
