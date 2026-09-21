"""Unit tests for the RBAC role classification (``app.auth.roles``).

These lock down the five built-in roles described in docs/RBAC_ROLES.md:
定位 (positioning), 默认操作点 (operations) and 说明 (description), plus the
normalisation rules that keep legacy ``owner`` / ``admin`` values working.
"""

from __future__ import annotations

import pytest

from app.auth import roles


EXPECTED_ROLES = [
    ("tenant_admin", "租户管理员", "租户内系统管理员，不做具体业务操作"),
    ("project_manager", "项目经理", "管理项目事项"),
    ("employee", "一般员工", "个人空间与任务执行"),
    ("expert", "专家", "协助审查"),
    ("viewer", "只读", "观察与审计"),
]

EXPECTED_OPERATIONS = {
    "tenant_admin": [
        "admin.user.manage",
        "admin.role.manage",
        "admin.org.manage",
        "admin.project.create",
        "admin.capability.manage",
        "admin.knowledge.manage",
        "admin.security.manage",
    ],
    "project_manager": [
        "project.graph.create",
        "project.graph.edit",
        "project.graph.assign",
        "project.review.confirm",
        "project.board.flow",
        "project.checklist.config",
        "project.report.export",
    ],
    "employee": [
        "personal.document.manage",
        "personal.skill.manage",
        "personal.agent.manage",
        "personal.inbox.manage",
        "personal.execute",
    ],
    "expert": ["review.review", "review.meeting", "review.invite"],
    "viewer": ["view.*"],
}


def test_catalogue_order_positioning_and_names():
    assert roles.role_codes() == tuple(code for code, _, _ in EXPECTED_ROLES)
    for code, name, positioning in EXPECTED_ROLES:
        role = roles.get_role(code)
        assert role is not None, code
        assert role.name == name
        assert role.positioning == positioning


def test_default_operations_per_role():
    for code, expected in EXPECTED_OPERATIONS.items():
        assert list(roles.operations_for(code)) == expected, code


def test_every_referenced_operation_point_exists():
    known = {operation.code for operation in roles.OPERATIONS}
    for code in roles.role_codes():
        for operation in roles.operations_for(code):
            assert operation in known, f"{code} references unknown {operation}"


def test_role_attributes_match_the_spec():
    assert roles.get_role("tenant_admin").unique is True
    assert roles.get_role("tenant_admin").assignable_to_business is False
    assert roles.get_role("employee").default_member is True

    # 默认唯一 + 默认成员角色 are each held by exactly one role.
    assert [r.code for r in roles.roles() if r.unique] == ["tenant_admin"]
    assert [r.code for r in roles.roles() if r.default_member] == ["employee"]

    # Only 租户管理员 / 一般员工 / 专家 / 项目经理 / 只读 - nothing else is a singleton.
    assert roles.enforces_singleton("tenant_admin") is True
    assert roles.enforces_singleton("project_manager") is False


def test_default_member_role():
    assert roles.default_role_code() == "employee"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("owner", "project_manager"),
        ("admin", "tenant_admin"),
        ("member", "employee"),
        ("guest", "viewer"),
        ("readonly", "viewer"),
        ("  ADMIN  ", "tenant_admin"),
        ("project_manager", "project_manager"),
        ("employee", "employee"),
        ("expert", "expert"),
        ("viewer", "viewer"),
    ],
)
def test_normalize_role_maps_known_values(raw, expected):
    assert roles.normalize_role(raw) == expected


@pytest.mark.parametrize("raw", ["root", "superuser", "", "   ", None, 42, "administrator"])
def test_normalize_role_never_escalates_unknown_values(raw):
    """Anything unrecognised must fall back to 一般员工, never to an admin role."""

    assert roles.normalize_role(raw) == "employee"


def test_normalize_role_is_idempotent():
    for code in roles.role_codes():
        assert roles.normalize_role(roles.normalize_role(code)) == code


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("tenant_admin", True),
        ("admin", True),
        ("ADMIN", True),
        ("project_manager", True),
        ("owner", True),
        ("employee", True),
        ("expert", True),
        ("viewer", True),
        ("root", False),
        ("", False),
        ("   ", False),
        (None, False),
        (123, False),
        ("administrator", False),
    ],
)
def test_is_valid_role_is_strict(raw, expected):
    """Validation must reject unknowns instead of silently downgrading them."""

    assert roles.is_valid_role(raw) is expected


def test_is_tenant_admin_only_for_admin_roles():
    assert roles.is_tenant_admin("tenant_admin") is True
    assert roles.is_tenant_admin("admin") is True
    for code in ("project_manager", "owner", "employee", "expert", "viewer", "root"):
        assert roles.is_tenant_admin(code) is False, code


def test_has_operation_grant_and_deny():
    # 项目经理 owns the graph / review / board / report points.
    assert roles.has_operation("project_manager", "project.graph.assign") is True
    assert roles.has_operation("project_manager", "admin.user.manage") is False
    # 一般员工 is limited to personal-space points.
    assert roles.has_operation("employee", "personal.document.manage") is True
    assert roles.has_operation("employee", "admin.user.manage") is False
    assert roles.has_operation("employee", "project.graph.edit") is False
    # 专家 only reviews.
    assert roles.has_operation("expert", "review.review") is True
    assert roles.has_operation("expert", "project.graph.edit") is False


def test_viewer_wildcard_matches_view_operations():
    assert roles.has_operation("viewer", "view.dashboard") is True
    assert roles.has_operation("viewer", "view.project.detail") is True
    # ...but nothing outside the view namespace.
    assert roles.has_operation("viewer", "project.graph.edit") is False
    assert roles.has_operation("viewer", "admin.user.manage") is False
    assert roles.has_operation("viewer", "view") is False


def test_effective_operations_is_admin_only_until_the_flag_opens_it():
    admin_only = roles.effective_operations("tenant_admin")
    assert list(admin_only) == EXPECTED_OPERATIONS["tenant_admin"]

    opened = roles.effective_operations(
        "tenant_admin", allow_tenant_admin_business=True
    )
    # The "可配置放开" switch adds the 项目经理 business points on top.
    assert set(EXPECTED_OPERATIONS["tenant_admin"]) <= set(opened)
    assert set(EXPECTED_OPERATIONS["project_manager"]) <= set(opened)
    assert len(opened) == len(set(EXPECTED_OPERATIONS["tenant_admin"]) | set(
        EXPECTED_OPERATIONS["project_manager"]
    ))

    # Other roles are unaffected by the flag.
    for code in ("project_manager", "employee", "expert", "viewer"):
        assert roles.effective_operations(code) == roles.effective_operations(
            code, allow_tenant_admin_business=True
        )


def test_can_be_assigned_business_role():
    assert roles.can_be_assigned_business_role("project_manager") is True
    assert roles.can_be_assigned_business_role("employee") is True
    assert roles.can_be_assigned_business_role("tenant_admin") is False
    assert (
        roles.can_be_assigned_business_role(
            "tenant_admin", allow_tenant_admin_business=True
        )
        is True
    )
    assert roles.can_be_assigned_business_role("root") is False


def test_catalog_serialisation_shape():
    catalogue = roles.role_catalog()
    assert [entry["code"] for entry in catalogue] == list(roles.role_codes())
    for entry in catalogue:
        assert set(entry) >= {
            "code",
            "name",
            "positioning",
            "operations",
            "operation_names",
            "description",
            "unique",
            "assignable_to_business",
            "default_member",
        }
        assert len(entry["operation_names"]) == len(entry["operations"])

    operations = roles.operation_catalog()
    assert any(op["code"] == "admin.user.manage" for op in operations)
    assert all({"code", "name", "group"} <= set(op) for op in operations)


def test_role_name_falls_back_to_the_raw_value():
    assert roles.role_name("tenant_admin") == "租户管理员"
    assert roles.role_name("admin") == "租户管理员"
    assert roles.role_name("brand_new_role") == "brand_new_role"
    assert roles.role_name(None) == "未分配"


def test_group_operations_buckets_by_category():
    grouped = roles.group_operations(roles.operations_for("project_manager"))
    assert [bucket["group"] for bucket in grouped] == [roles.GROUP_PROJECT]
    assert len(grouped[0]["operations"]) == 7
