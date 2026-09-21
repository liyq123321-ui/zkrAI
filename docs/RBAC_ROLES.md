# 角色与权限分类（RBAC）

本文件是 firstFlight 登录 / 权限层角色分类的权威说明。代码上的单一事实来源是
[`backend/app/auth/roles.py`](../backend/app/auth/roles.py)，前端标签见
[`frontend/aios-main/src/auth/session.ts`](../frontend/aios-main/src/auth/session.ts)。

## 角色分类

| 角色 | code | 定位 | 默认操作点 | 说明 |
|------|------|------|-----------|------|
| 租户管理员 | `tenant_admin` | 租户内系统管理员，不做具体业务操作 | 全部管理类操作点（用户 / 角色 / 组织 / 项目创建 / 能力 / 知识 / 系统安全） | 默认唯一，由平台管理员创建；默认不可被授予业务角色（可配置放开） |
| 项目经理 | `project_manager` | 管理项目事项 | 图与节点（创建 / 编辑 / 分派）、审查确认、看板流转、检查条目配置、项目报告导出 | 可复制、改名、改勾选 |
| 一般员工 | `employee` | 个人空间与任务执行 | 个人文档、技能、分身管理、消息待办、执行与提交审查 | 默认成员角色 |
| 专家 | `expert` | 协助审查 | 审查、评审会、邀请评审 | 受邀进入项目 |
| 只读 | `viewer` | 观察与审计 | 查看类操作点 | 最小权限 |

角色是**扁平**的：一个账号只有一个 `users.role`，不做角色继承。分类表会通过
`GET /api/auth/roles` 下发给前端渲染，前端不硬编码。

## 当前分配状态

| 账号 | 角色 | 说明 |
|------|------|------|
| `owner-1` | 项目经理（`project_manager`） | 既有内容都归属该账号，是当前主角色 |
| `admin` | 租户管理员（`tenant_admin`） | 唯一的管理类账号 |
| 一般员工 / 专家 / 只读 | **暂不分配** | 需要时在「用户管理 · 登录与权限」里创建并指定 |

## 操作点（操作点清单）

每个角色默认携带的操作点，code 稳定、可直接用于鉴权判断。

| 分组 | 操作点 code | 名称 | 归属角色 |
|------|------------|------|---------|
| 管理类 | `admin.user.manage` | 用户管理 | 租户管理员 |
| 管理类 | `admin.role.manage` | 角色管理 | 租户管理员 |
| 管理类 | `admin.org.manage` | 组织管理 | 租户管理员 |
| 管理类 | `admin.project.create` | 项目创建 | 租户管理员 |
| 管理类 | `admin.capability.manage` | 能力管理 | 租户管理员 |
| 管理类 | `admin.knowledge.manage` | 知识管理 | 租户管理员 |
| 管理类 | `admin.security.manage` | 系统安全 | 租户管理员 |
| 项目类 | `project.graph.create` | 图与节点 · 创建 | 项目经理 |
| 项目类 | `project.graph.edit` | 图与节点 · 编辑 | 项目经理 |
| 项目类 | `project.graph.assign` | 图与节点 · 分派 | 项目经理 |
| 项目类 | `project.review.confirm` | 审查确认 | 项目经理 |
| 项目类 | `project.board.flow` | 看板流转 | 项目经理 |
| 项目类 | `project.checklist.config` | 检查条目配置 | 项目经理 |
| 项目类 | `project.report.export` | 项目报告导出 | 项目经理 |
| 个人类 | `personal.document.manage` | 个人文档 | 一般员工 |
| 个人类 | `personal.skill.manage` | 技能 | 一般员工 |
| 个人类 | `personal.agent.manage` | 分身管理 | 一般员工 |
| 个人类 | `personal.inbox.manage` | 消息待办 | 一般员工 |
| 个人类 | `personal.execute` | 执行与提交审查 | 一般员工 |
| 专家类 | `review.review` | 审查 | 专家 |
| 专家类 | `review.meeting` | 评审会 | 专家 |
| 专家类 | `review.invite` | 邀请评审 | 专家 |
| 查看类 | `view.*` | 查看类操作点 | 只读 |

`view.*` 是前缀通配，匹配所有 `view.` 开头的操作点。

## 后端接入

### 鉴权依赖

```python
from app.auth.deps import require_tenant_admin, require_operation

@router.get("/api/admin/users")
def list_users(_admin=Depends(require_tenant_admin)): ...

@router.post("/nodes", dependencies=[Depends(require_operation("project.graph.create"))])
def create_node(): ...
```

`require_admin` 保留为 `require_tenant_admin` 的别名，旧代码无需改动。

### 接口

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET /api/auth/roles` | 任意登录用户 | 角色分类 + 操作点清单（前端渲染分类表用） |
| `POST /api/auth/login` | 公开 | 返回 `role` / `role_name` / `operations` |
| `GET /api/auth/me` | 登录用户 | 同上 |
| `GET /api/admin/users` | 租户管理员 | 用户列表，含 `role_name` |
| `POST /api/admin/users` | 租户管理员 | 创建账号；`role` 必须是五个 code 之一 |

### 登录返回示例

```json
{
  "username": "admin",
  "role": "tenant_admin",
  "role_name": "租户管理员",
  "operations": [
    "admin.user.manage", "admin.role.manage", "admin.org.manage",
    "admin.project.create", "admin.capability.manage",
    "admin.knowledge.manage", "admin.security.manage"
  ],
  "is_active": true
}
```

## 关键规则

1. **默认成员角色 = 一般员工。** 创建账号不传 `role` 时落到 `employee`（`default_role_code()`）。
2. **租户管理员默认唯一。** 已有活跃租户管理员时，再创建或启用第二个会返回
   `400 TENANT_ADMIN_EXISTS`。最后一个活跃租户管理员不可被删除（`LAST_ADMIN`）。
3. **非法角色一律拒绝，绝不静默降级。** 未知角色返回 `422 INVALID_ROLE`，并回传
   `valid_roles` 列表。校验用的是严格版 `is_valid_role()`，而不是会回退的
   `normalize_role()`——否则任何输入都会被"当作一般员工"接受。
4. **历史值兼容。** `normalize_role()` 把 `owner → project_manager`、
   `admin → tenant_admin`、`member → employee`、`guest/readonly → viewer` 等映射到
   规范 code；无法识别时**回退到一般员工**（最小权限，绝不会意外提权）。
5. **查询 / 鉴权路径严格。** `get_role()` 对未知值返回 `None`，
   `has_operation()` 因此默认拒绝；`role_name()` 对未知值原样返回，便于发现脏数据。

## 配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RBAC_ALLOW_TENANT_ADMIN_BUSINESS` | `false` | 设为 `true` 后，租户管理员在管理类操作点之外**叠加**项目经理的业务操作点（即"可配置放开"）。默认关闭，租户管理员只做管理、不做业务 |

## 数据迁移

`users.role` 是既有字段，旧值 `owner` / `admin` 在应用启动时由
`_migrate_sqlite_user_roles()`（见
[`backend/app/database/database.py`](../backend/app/database/database.py)）就地归一化：

- 已规范的值原样保留（幂等，可重复执行）；
- 其余值逐个通过 `normalize_role()` 解析后更新。

因为映射逻辑直接调用角色目录，迁移**不会与分类表发生漂移**。种子逻辑
（`app/auth/seed.py`）也会顺手修正内置账号的历史角色值。

## 前端

- `session.ts`：`RoleCode` 联合类型、`ROLE_LABELS`、`normalizeRole()`、`roleLabel()`、
  `isTenantAdmin()`（`isAdmin()` 保留为别名）、`userOperations()` / `hasOperation()`。
- `SettingsPanel`：显示角色中文名；「用户管理」入口仅对**租户管理员**可见。
- `AdminUsersPage`：创建账号时可从五个角色中选择（默认一般员工），列表展示角色中文名 +
  code，并附**角色分类表**（角色 / 定位 / 默认操作点 / 说明）。分类表从
  `GET /api/auth/roles` 读取，取不到时用内置兜底副本，不会挡住账号列表。

## 测试

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_roles.py tests/integration/test_rbac_roles.py -q
cd frontend/aios-main && ./node_modules/.bin/vitest run src/auth/session.test.ts
```

覆盖内容：分类表顺序与文案、操作点归属、通配匹配、别名归一化与"未知值不提权"、
`tenant_admin` 唯一性、非法角色拒绝、历史值兼容、默认成员角色、非管理员越权拦截。
