"""Canonical role classification for the login / permission layer.

This module is the single source of truth for *who a user is* in firstFlight.
Every role carries four pieces of information that the product spec calls for:

``定位`` (positioning)      - what the role is for, in one line.
``默认操作点`` (operations)  - the operation points granted by default, as
                             stable machine-readable codes drawn from
                             :data:`OPERATIONS`.
``说明`` (description)       - the rules that govern the role itself.

The five built-in roles are:

======================  ============================================
code                    name
======================  ============================================
``tenant_admin``        租户管理员
``project_manager``     项目经理
``employee``            一般员工
``expert``              专家
``viewer``              只读
======================  ============================================

Design notes
------------
* Roles are **flat** - a user holds exactly one ``User.role`` string, matching
  the existing ``users`` table. No role inheritance is modelled here.
* ``owner`` and ``admin`` are **legacy** codes. :func:`normalize_role` maps them
  onto ``project_manager`` / ``tenant_admin`` so old rows and old tokens keep
  working without a hard migration cutoff.
* ``tenant_admin`` is unique by default and is not a business role: it manages
  accounts, not projects. ``rbac_allow_tenant_admin_business`` can relax that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# --- role codes -------------------------------------------------------------
ROLE_TENANT_ADMIN = "tenant_admin"
ROLE_PROJECT_MANAGER = "project_manager"
ROLE_EMPLOYEE = "employee"
ROLE_EXPERT = "expert"
ROLE_VIEWER = "viewer"

# --- operation-point groups -------------------------------------------------
GROUP_ADMIN = "管理类"
GROUP_PROJECT = "项目类"
GROUP_PERSONAL = "个人类"
GROUP_REVIEW = "专家类"
GROUP_VIEW = "查看类"

# Wildcard suffix: ``view.*`` matches every ``view.``-prefixed operation point.
WILDCARD = "*"


@dataclass(frozen=True)
class OperationPoint:
    """A single grantable action (an "操作点")."""

    code: str
    name: str
    group: str


@dataclass(frozen=True)
class RoleDefinition:
    """A built-in role and the default operation points it carries."""

    code: str
    name: str
    positioning: str
    operations: tuple[str, ...]
    description: str
    # 默认唯一：tenant-scoped singleton, created by the platform admin.
    unique: bool = False
    # 默认不可被授予业务角色（可配置放开）：a management role, not a project role.
    assignable_to_business: bool = True
    # 默认成员角色：the role new members get when none is specified.
    default_member: bool = False
    # Ordering hint for UIs rendering the catalogue.
    order: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "positioning": self.positioning,
            "operations": list(self.operations),
            "operation_names": [operation_name(code) for code in self.operations],
            "description": self.description,
            "unique": self.unique,
            "assignable_to_business": self.assignable_to_business,
            "default_member": self.default_member,
        }


# --- operation-point catalogue ---------------------------------------------
# Ordered to match the "默认操作点" wording in the product spec.
OPERATIONS: tuple[OperationPoint, ...] = (
    # 管理类 —— 租户管理员
    OperationPoint("admin.user.manage", "用户管理", GROUP_ADMIN),
    OperationPoint("admin.role.manage", "角色管理", GROUP_ADMIN),
    OperationPoint("admin.org.manage", "组织管理", GROUP_ADMIN),
    OperationPoint("admin.project.create", "项目创建", GROUP_ADMIN),
    OperationPoint("admin.capability.manage", "能力管理", GROUP_ADMIN),
    OperationPoint("admin.knowledge.manage", "知识管理", GROUP_ADMIN),
    OperationPoint("admin.security.manage", "系统安全", GROUP_ADMIN),
    # 项目类 —— 项目经理
    OperationPoint("project.graph.create", "图与节点 · 创建", GROUP_PROJECT),
    OperationPoint("project.graph.edit", "图与节点 · 编辑", GROUP_PROJECT),
    OperationPoint("project.graph.assign", "图与节点 · 分派", GROUP_PROJECT),
    OperationPoint("project.review.confirm", "审查确认", GROUP_PROJECT),
    OperationPoint("project.board.flow", "看板流转", GROUP_PROJECT),
    OperationPoint("project.checklist.config", "检查条目配置", GROUP_PROJECT),
    OperationPoint("project.report.export", "项目报告导出", GROUP_PROJECT),
    # 个人类 —— 一般员工
    OperationPoint("personal.document.manage", "个人文档", GROUP_PERSONAL),
    OperationPoint("personal.skill.manage", "技能", GROUP_PERSONAL),
    OperationPoint("personal.agent.manage", "分身管理", GROUP_PERSONAL),
    OperationPoint("personal.inbox.manage", "消息待办", GROUP_PERSONAL),
    OperationPoint("personal.execute", "执行与提交审查", GROUP_PERSONAL),
    # 专家类 —— 专家
    OperationPoint("review.review", "审查", GROUP_REVIEW),
    OperationPoint("review.meeting", "评审会", GROUP_REVIEW),
    OperationPoint("review.invite", "邀请评审", GROUP_REVIEW),
    # 查看类 —— 只读
    OperationPoint("view.*", "查看类操作点", GROUP_VIEW),
)

_OPERATION_INDEX: Mapping[str, OperationPoint] = {op.code: op for op in OPERATIONS}


# --- the five built-in roles ------------------------------------------------
ROLE_DEFINITIONS: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        code=ROLE_TENANT_ADMIN,
        name="租户管理员",
        positioning="租户内系统管理员，不做具体业务操作",
        operations=(
            "admin.user.manage",
            "admin.role.manage",
            "admin.org.manage",
            "admin.project.create",
            "admin.capability.manage",
            "admin.knowledge.manage",
            "admin.security.manage",
        ),
        description=(
            "默认唯一，由平台管理员创建；默认不可被授予业务角色（可配置放开）。"
        ),
        unique=True,
        assignable_to_business=False,
        order=1,
    ),
    RoleDefinition(
        code=ROLE_PROJECT_MANAGER,
        name="项目经理",
        positioning="管理项目事项",
        operations=(
            "project.graph.create",
            "project.graph.edit",
            "project.graph.assign",
            "project.review.confirm",
            "project.board.flow",
            "project.checklist.config",
            "project.report.export",
        ),
        description="可复制、改名、改勾选。",
        order=2,
    ),
    RoleDefinition(
        code=ROLE_EMPLOYEE,
        name="一般员工",
        positioning="个人空间与任务执行",
        operations=(
            "personal.document.manage",
            "personal.skill.manage",
            "personal.agent.manage",
            "personal.inbox.manage",
            "personal.execute",
        ),
        description="默认成员角色。",
        default_member=True,
        order=3,
    ),
    RoleDefinition(
        code=ROLE_EXPERT,
        name="专家",
        positioning="协助审查",
        operations=("review.review", "review.meeting", "review.invite"),
        description="受邀进入项目。",
        order=4,
    ),
    RoleDefinition(
        code=ROLE_VIEWER,
        name="只读",
        positioning="观察与审计",
        operations=("view.*",),
        description="最小权限。",
        order=5,
    ),
)

_ROLE_INDEX: Mapping[str, RoleDefinition] = {role.code: role for role in ROLE_DEFINITIONS}

# Legacy / alias codes seen in older rows, tokens, and API payloads.
ROLE_ALIASES: Mapping[str, str] = {
    # pre-RBAC free-text codes
    "owner": ROLE_PROJECT_MANAGER,
    "admin": ROLE_TENANT_ADMIN,
    # common shorthands
    "tenant-admin": ROLE_TENANT_ADMIN,
    "tenantadmin": ROLE_TENANT_ADMIN,
    "project-manager": ROLE_PROJECT_MANAGER,
    "projectmanager": ROLE_PROJECT_MANAGER,
    "pm": ROLE_PROJECT_MANAGER,
    "member": ROLE_EMPLOYEE,
    "staff": ROLE_EMPLOYEE,
    "user": ROLE_EMPLOYEE,
    "specialist": ROLE_EXPERT,
    "readonly": ROLE_VIEWER,
    "read_only": ROLE_VIEWER,
    "guest": ROLE_VIEWER,
    "viewer": ROLE_VIEWER,
    # identity mappings so normalisation is idempotent
    ROLE_TENANT_ADMIN: ROLE_TENANT_ADMIN,
    ROLE_PROJECT_MANAGER: ROLE_PROJECT_MANAGER,
    ROLE_EMPLOYEE: ROLE_EMPLOYEE,
    ROLE_EXPERT: ROLE_EXPERT,
}

DEFAULT_MEMBER_ROLE = next(
    role.code for role in ROLE_DEFINITIONS if role.default_member
)


# --- lookups ----------------------------------------------------------------
def role_codes() -> tuple[str, ...]:
    """Every valid role code, in catalogue order."""

    return tuple(role.code for role in ROLE_DEFINITIONS)


def roles() -> tuple[RoleDefinition, ...]:
    return ROLE_DEFINITIONS


def get_role(code: str | None) -> RoleDefinition | None:
    """Resolve a role code to its definition, or ``None`` if unrecognised.

    Deliberately **strict**: unlike :func:`normalize_role` it does not fall back
    to 一般员工, because every caller here describes or authorises a specific
    role. Silently resolving an unknown value to a real role would mislabel
    accounts and grant the employee operation points to garbage input.
    """

    if not isinstance(code, str):
        return None
    cleaned = code.strip().lower()
    if cleaned in _ROLE_INDEX:
        return _ROLE_INDEX[cleaned]
    canonical = ROLE_ALIASES.get(cleaned)
    if canonical is None:
        return None
    return _ROLE_INDEX.get(canonical)


def role_name(code: str | None) -> str:
    """Chinese display name for a role, falling back to the raw value."""

    role = get_role(code)
    if role is not None:
        return role.name
    return code if isinstance(code, str) and code else "未分配"


def is_valid_role(code: Any) -> bool:
    """Whether ``code`` is a canonical role or a recognised alias.

    Deliberately strict: it must NOT be implemented in terms of
    :func:`normalize_role`, because that falls back to 一般员工 for anything it
    does not recognise and would therefore accept every input.
    """

    if not isinstance(code, str):
        return False
    cleaned = code.strip().lower()
    return cleaned in _ROLE_INDEX or cleaned in ROLE_ALIASES


def normalize_role(code: Any, *, default: str = DEFAULT_MEMBER_ROLE) -> str:
    """Map any incoming role value onto a canonical code.

    Unknown values fall back to ``default`` (一般员工) so a stale row or a typo
    can never grant elevated access.
    """

    if not isinstance(code, str):
        return default
    cleaned = code.strip().lower()
    if not cleaned:
        return default
    if cleaned in _ROLE_INDEX:
        return cleaned
    return ROLE_ALIASES.get(cleaned, default)


def is_tenant_admin(code: Any) -> bool:
    """Whether the value maps to the tenant administrator role."""

    return normalize_role(code) == ROLE_TENANT_ADMIN


def enforces_singleton(code: Any) -> bool:
    """Whether only one active account may hold this role."""

    role = get_role(code)
    return bool(role and role.unique)


def default_role_code() -> str:
    """The role assigned when a new member is created without an explicit one."""

    return DEFAULT_MEMBER_ROLE


# --- operation points -------------------------------------------------------
def operation_name(code: str) -> str:
    point = _OPERATION_INDEX.get(code)
    if point is not None:
        return point.name
    if code.endswith("." + WILDCARD):
        return f"{code[: -len(WILDCARD)]}全部"
    return code


def has_operation(role_code: Any, operation: str) -> bool:
    """Whether ``role_code`` grants ``operation`` by default.

    Supports the ``<prefix>.*`` wildcard used by the 只读 role.
    """

    role = get_role(role_code)
    if role is None or not isinstance(operation, str) or not operation:
        return False
    for granted in role.operations:
        if granted == operation:
            return True
        if granted.endswith("." + WILDCARD):
            prefix = granted[: -len(WILDCARD)]
            if operation.startswith(prefix):
                return True
    return False


def operations_for(role_code: Any) -> tuple[str, ...]:
    role = get_role(role_code)
    return role.operations if role is not None else ()


def can_be_assigned_business_role(
    role_code: Any, *, allow_tenant_admin_business: bool = False
) -> bool:
    """Whether this role may also carry business (project) duties.

    租户管理员 is excluded by default; ``allow_tenant_admin_business`` relaxes it
    (wired to ``RBAC_ALLOW_TENANT_ADMIN_BUSINESS``).
    """

    role = get_role(role_code)
    if role is None:
        return False
    if role.assignable_to_business:
        return True
    return role.code == ROLE_TENANT_ADMIN and allow_tenant_admin_business


def effective_operations(
    role_code: Any, *, allow_tenant_admin_business: bool = False
) -> tuple[str, ...]:
    """The operation points a user *actually* holds.

    默认情况下 租户管理员 只有管理类操作点、不带业务操作点。When
    ``allow_tenant_admin_business`` is enabled the tenant administrator also
    carries the 项目经理 operation points - that is the "可配置放开" switch.
    """

    role = get_role(role_code)
    if role is None:
        return ()
    if role.code != ROLE_TENANT_ADMIN or not allow_tenant_admin_business:
        return role.operations
    manager = _ROLE_INDEX[ROLE_PROJECT_MANAGER]
    merged = list(role.operations)
    for code in manager.operations:
        if code not in merged:
            merged.append(code)
    return tuple(merged)


# --- serialisation for the API / frontend -----------------------------------
def operation_catalog() -> list[dict[str, Any]]:
    return [
        {"code": op.code, "name": op.name, "group": op.group} for op in OPERATIONS
    ]


def role_catalog() -> list[dict[str, Any]]:
    """The whole classification table, ready to ship as JSON."""

    return [role.as_dict() for role in ROLE_DEFINITIONS]


def group_operations(codes: Iterable[str]) -> list[dict[str, Any]]:
    """Group a role's operation points by their 分类, for table rendering."""

    grouped: dict[str, list[dict[str, str]]] = {}
    for code in codes:
        point = _OPERATION_INDEX.get(code)
        group = point.group if point is not None else GROUP_VIEW
        name = point.name if point is not None else operation_name(code)
        grouped.setdefault(group, []).append({"code": code, "name": name})
    return [{"group": group, "operations": items} for group, items in grouped.items()]
