# firstFlight 前后端对接契约（第一阶段）

## 1. 文档状态

- 状态：核心第一阶段已实现并通过自动化回归（2026-09-02）
- 目标：把现有 React mock 前端接入现有 FastAPI 后端，形成可真实使用的项目规划、规格审核、任务拆解与 Gitea PRD 批注系统。
- 权威实现：后端现有 Session、Spec、WorkItem、Agent Spec、Audit Event 与 Gitea PRD Review 服务。
- 兼容原则：尽量保留现有后端路由和业务语义，通过前端 API 适配层消化视图模型差异。

## 2. 已确认的产品决策

1. 第一阶段只交付项目澄清、Spec 生成与审核、任务拆解、Agent Spec 查询、PRD 行批注和 PRD 演进。
2. 第一阶段不承诺真实子 Agent 执行、代码生成、测试、部署、实时协作、代码仓库浏览或提交历史。
3. 用户身份由服务端代填；浏览器不拥有可信 `actor_id`。
4. PRD 批注采用 Gitea diff 行号语义，不采用章节 ID 或引用文本作为权威锚点。
5. 历史版本恢复通过“基于历史 revision 创建新的 n+1 revision”实现，不移动或覆盖历史版本。
6. 前端只显示安全的阶段进度和审计摘要。
7. 后台保存可审计执行轨迹，不依赖、展示或持久化模型私有原始思维链。
8. 用户可在初始澄清阶段显式跳过澄清；模糊地带由 Agent 采用保守假设处理，并在 PRD 中交给人类批改。

## 3. 第一阶段边界

### 3.1 包含

- 创建项目 Session。
- 回答澄清问题。
- 通过按钮或明确自然语言意图跳过初始澄清并直接生成 PRD。
- 创建、修订、批准、驳回或要求返工 Spec。
- 从已批准 Spec 生成 WorkItem、依赖和 Agent Spec。
- 查询 Session 状态、Spec、WorkItem、Agent Spec 和安全审计事件。
- 获取当前或历史 PRD。
- 查询、创建、回复、解决或恢复 Gitea PRD 行批注。
- 冻结未解决批注，异步生成下一版 PRD，并查询任务结果。
- 从历史 Spec/PRD revision 创建新的最新 revision。

### 3.2 不包含

- 执行 WorkItem 对应的子 Agent。
- 任意拖拽修改 WorkItem 执行状态。
- 真实代码工作区、Git commit/diff 浏览、测试、部署或 WebSocket 协同。
- 在浏览器中保存 Gitea token、Agent provider key 或可信用户身份。
- 展示模型原始推理或敏感工具输入输出。

## 4. 总体架构

```text
React UI
  -> src/api (HTTP/SSE 客户端、DTO、错误与重试)
  -> FastAPI（身份代填、Session API、PRD API）
  -> SQLite（业务状态、命令、任务、审计与恢复证据）
  -> AgentGateway（PM/Reviewer Agent）
  -> Gitea（PRD 文件、PR、评论和回复权威）
```

约束：

- React 组件不得直接调用 `fetch`。
- React 组件只消费前端 view model。
- `src/api` 负责后端 DTO 到 view model 的转换。
- 后端返回的 `phase`、`current_spec_status`、`legal_actions` 和 `state_version` 是工作流权威。
- Gitea PRD Markdown、commit 和评论状态仍以 Gitea 为权威。

## 5. 身份代填契约

### 5.1 第一阶段本地模式

后端新增：

```env
FIRSTFLIGHT_IDENTITY_MODE=local
FIRSTFLIGHT_ACTOR_ID=owner-1
```

规则：

- `local` 模式下，后端从 `FIRSTFLIGHT_ACTOR_ID` 获取 actor。
- 所有写操作忽略浏览器提交的 `actor_id`；浏览器可以完全不发送该字段。
- `FIRSTFLIGHT_ACTOR_ID` 必须存在于项目的 `final_approver`、`project_manager_ids` 或 `root_owner_ids` 中，具体权限仍由现有业务规则判断。
- 本地模式默认仅绑定 loopback，禁止直接暴露到公网。

### 5.2 后续代理模式

```env
FIRSTFLIGHT_IDENTITY_MODE=proxy
FIRSTFLIGHT_TRUSTED_ACTOR_HEADER=X-FirstFlight-Actor
```

- 只有可信反向代理可以写入身份头。
- 应用直连端口不得暴露给终端用户。
- 后端覆盖或拒绝浏览器正文中的 `actor_id`。
- JWT 校验属于代理层或后续扩展，不影响业务服务接口。

### 5.3 兼容策略

后端内部 Service 仍显式接收 `actor_id`。HTTP 边界使用统一的 `ActorResolver` 解析 actor 后再构造 Service 请求，避免身份逻辑进入业务服务。

## 6. API 基础约定

### 6.1 地址

前端新增：

```env
VITE_API_BASE_URL=http://127.0.0.1:8088
```

- 第一阶段不强制修改现有后端路由前缀。
- 生产环境优先同源部署；不同源时后端只允许显式配置的前端 origin。
- 不使用前端文档中尚未实现的 `/v1/work-items`、`/v1/topology/dag` 或 `/v1/vcs/commits`。

### 6.2 成功响应

保持后端现有 Pydantic JSON，不增加 `{code, data}` 包装层。

### 6.3 错误响应

```json
{
  "detail": {
    "code": "STABLE_ERROR_CODE",
    "message": "Public safe message",
    "errors": []
  }
}
```

前端统一转换为：

```ts
interface ApiError {
  status: number;
  code: string;
  message: string;
  validationErrors?: unknown[];
  retryable: boolean;
}
```

基本处理：

- `400/403/404/422`：显示明确、可操作的错误。
- `409 STALE_STATE`：重新读取 Session 状态，不自动重放原命令。
- 其他 `409`：刷新相关资源并提示冲突。
- `503`：允许用户显式重试；不盲目重试可能产生外部副作用的命令。

## 7. 前端 API 客户端模块

建议目录：

```text
src/api/
  client.ts
  errors.ts
  identity.ts
  sessions.ts
  chat.ts
  prd.ts
  mappers.ts
  dto.ts
```

职责：

- `client.ts`：Base URL、JSON、超时和请求取消。
- `errors.ts`：解析稳定错误结构。
- `identity.ts`：不保存真实 actor，只提供当前部署模式信息。
- `sessions.ts`：Session 命令和查询。
- `chat.ts`：解析 `/chat` SSE。
- `prd.ts`：PRD、评论和发布任务。
- `mappers.ts`：后端 DTO 到现有 UI 类型的显式映射。
- `dto.ts`：只描述后端契约，不混入 UI 装饰字段。

## 8. Session 与 Spec 契约

### 8.1 现有接口

| 前端方法 | 后端接口 | 说明 |
| --- | --- | --- |
| `createSession` | `POST /sessions` | 创建项目并执行初次 PM 分析 |
| `executeCommand` | `POST /sessions/{sessionId}/commands` | 所有状态变化的统一入口 |
| `getSessionState` | `GET /sessions/{sessionId}/state` | 读取最新状态和合法动作 |
| `listSpecs` | `GET /sessions/{sessionId}/specs` | 获取不可变 Spec 历史 |
| `getSpec` | `GET /sessions/{sessionId}/specs/{version}` | 按 revision 或 ID 获取 Spec |
| `listWorkItems` | `GET /sessions/{sessionId}/work-items` | 读取拆解结果 |
| `getWorkItem` | `GET /sessions/{sessionId}/work-items/{id}` | 读取单项及依赖 |
| `listAgentSpecs` | `GET /sessions/{sessionId}/agent-specs` | 读取 Agent Spec |
| `listEvents` | `GET /sessions/{sessionId}/events` | 读取安全审计事件 |

### 8.2 命令幂等与并发

- `request_id`：创建意图的稳定 UUID；提交后持久化到浏览器的 Session 记录中。
- `command_id`：每次用户明确动作生成一个 UUID；同一动作重试必须复用同一 ID。
- `expected_state_version`：始终取自最新 SessionState。
- 命令成功后用响应中的完整 `state` 原子替换本地状态。
- 遇到 `STALE_STATE` 时重新读取状态，让用户确认是否重新执行。
- 前端超时预算覆盖正常成功路径中的后端 Agent 执行预算：普通事务 130 秒、单 Agent 2075 秒、双 Agent 串联 4150 秒；每次后端 Agent 调用上限为 2000 秒。结构化输出重试或拆解语义修复会增加调用次数，因此这不是覆盖所有理论重试轮次的保证；长路径可触发 `REQUEST_TIMEOUT`，再走既有状态刷新与重试恢复。
- 内部计时器触发使用 `REQUEST_TIMEOUT`，调用方取消使用 `REQUEST_ABORTED`；超时后读取最新 Session，避免把后端已完成误报为失败。
- 不对 `approve`、`reject`、`rework`、`revise`、`convert_to_work_item` 做静默自动重试。
- `convert_to_work_item` 在后端分为基础任务树、逐任务计划和最终审核三个阶段；逐任务计划最多并发两个。任一阶段失败时前端仍只展示一次命令失败，不展示或拼接后端半成品。
- 用户显式重试拆解时，后端可复用同一项目和已批准 Spec 快照下已验证的基础结果及任务计划，只补缺失项。检查点以项目/Spec/AgentCall UUID 和内容哈希绑定，不使用项目名、任务名或 `FR-*` 名称查找；Spec 换版后不会复用。

### 8.3 UI 合法动作

前端按钮只由 `legal_actions` 驱动：

| 后端 action | UI 动作 |
| --- | --- |
| `message` | 回答澄清 |
| `skip_clarification` | 记录用户接受 Agent 假设，并进入 PRD 生成 |
| `create_spec` | 生成/恢复自动审核 |
| `approve` | 人工通过 |
| `reject` | 人工驳回 |
| `rework` | 要求返工 |
| `revise` | 生成修订版 |
| `convert_to_work_item` | 生成 WorkItem 与 Agent Spec |

不得因为 UI 认为“下一步合理”而绕过 `legal_actions`。WebGUI 只在后端同时公开 `skip_clarification` 时识别明确的跳过短语；识别后发送该命令，并使用返回的新状态自动发送 `create_spec`。后端负责权限、CAS、幂等和审计。

## 9. WorkItem 与前端视图映射

### 9.1 类型映射

| 后端 `kind` | 前端展示类型 |
| --- | --- |
| `ROOT` | `epic` |
| `MILESTONE` | `story` |
| `TASK` | `task` |

### 9.2 字段映射

- `dependency_work_item_ids` 用于派生只读 DAG 边。
- `responsible_role`、`suggested_assignee` 用于选择本地 Agent 头像和颜色；装饰信息不回写后端。
- 前端 `estimatedHours`、`loggedHours`、`progress`、`commits`、`auditLogs` 若后端无事实来源，显示为空或“未提供”，不得继续展示 mock 值。
- 第一阶段看板为只读视图，不允许拖拽状态或自动解除阻塞。
- DAG 只表达规划依赖，不表达真实运行、延迟或部署状态。

## 10. Agent 对话与阶段进度

### 10.1 现有 SSE 接口

`POST /chat` 可能返回：

- `session.created`
- `clarification.requested`
- `spec.ready`
- `workflow.error`
- `next_action`

前端将事件转换为安全的阶段卡片。第一阶段不期待 token、`tool_call`、完整 thought chain 或 WebSocket。

### 10.2 可审计执行轨迹

后台可保存以下结构化事件：

```json
{
  "trace_id": "trace-uuid",
  "agent_call_id": "agent-call-id",
  "sequence": 1,
  "event_type": "phase_started",
  "phase": "pm_analysis",
  "summary": "正在检查需求完整性",
  "tool_name": null,
  "input_hash": "sha256",
  "output_hash": null,
  "status": "running",
  "started_at": "ISO-8601",
  "completed_at": null,
  "safe_error_code": null
}
```

要求：

- 若模型提供 reasoning summary，可保存为安全摘要。
- 工具调用、检查点、状态变更和最终结构化输出必须可审计。
- 原始 stderr、敏感 prompt、token、凭据和模型私有推理不得进入公开 `/events`。
- 受限诊断数据继续保存在 `AgentCall` 或专用受限表中。
- 前端只读取安全事件，不读取受限诊断记录。

## 11. PRD 行批注契约

### 11.1 现有接口

| 前端方法 | 后端接口 |
| --- | --- |
| `getLatestPrd` | `GET /prd/{wi}` |
| `getPrdVersion` | `GET /prd/{wi}/v/{number}` |
| `listPrdVersions` | `GET /prd/{wi}/versions` |
| `listPrdComments` | `GET /prd/{wi}/comments` |
| `createPrdComment` | `POST /prd/{wi}/comments` |
| `replyPrdComment` | `POST /prd/{wi}/comments/{commentId}/reply` |
| `resolvePrdComment` | `POST /prd/{wi}/comments/{commentId}/resolve` |
| `publishPrdReview` | `POST /prd/{wi}/reviews/publish` |
| `getTask` | `GET /tasks/{taskId}` |

`wi` 必须是根 WorkItem ID，不等同于任意看板工单 ID。

### 11.2 可批注行扩展

现有 `GET /prd/{wi}` 不告诉前端哪些行属于 Gitea diff 的有效 context/addition 行。新增：

```text
GET /prd/{wi}/commentable-lines
```

建议响应：

```json
{
  "wi": "root-1",
  "version": 1,
  "filename": "docs/prd/root-1/v1.md",
  "commit_sha": "abcdef",
  "lines": [
    {"line": 1, "kind": "addition", "text": "# Project PRD"},
    {"line": 2, "kind": "context", "text": ""}
  ]
}
```

- `line` 必须与 `POST /comments` 接收的行号完全一致。
- 前端只在返回的行上显示“添加批注”。
- 后端创建评论时仍执行最终 diff 验证，防止页面数据过期。
- `commit_sha` 变化后，前端必须刷新 PRD 和可批注行。

### 11.3 批注提交和发布流程

```text
加载 PRD + 可批注行 + 评论
  -> 用户在允许行上暂存批注
  -> 逐条 POST /comments
  -> 显示每条提交结果
  -> POST /reviews/publish
  -> 收到 202 task_id
  -> 轮询 GET /tasks/{task_id}
  -> done 后刷新 PRD versions、latest、comments 和 Session state
```

规则：

- 多条评论不是原子批量接口；前端必须允许部分成功并标出失败项。
- 发布按钮只在至少一条权威未解决评论存在时启用。
- `pending/processing` 时显示安全阶段进度并禁用重复创建新任务。
- `error` 时显示稳定错误，并保留原批注状态供恢复。
- Agent 回复不自动等于人工 resolve。
- 人工需要显式调用 resolve/unresolve。
- 前端不生成 Markdown、commit hash、新版号或“已解决”结果。

### 11.4 轮询策略

- 首次等待 500ms。
- 随后 1s、2s，最大间隔 3s。
- 页面切后台时降低频率；页面恢复时立即查询一次。
- 使用 `AbortController` 在离开页面时取消请求。
- 客户端超时不代表任务失败；重新进入页面可继续查询同一 task。

## 12. 历史版本恢复契约

建议新增公开命令 `restore_spec_version`，仍走 Session command API：

```json
{
  "command_id": "uuid",
  "action": "restore_spec_version",
  "expected_state_version": 12,
  "message": "恢复 v2 的业务内容，并作为新的最新版本重新审核",
  "payload": {
    "source_revision": 2
  }
}
```

语义：

- 读取历史 revision 的结构化 Spec 内容。
- 以当前 Spec 为 parent，创建新的 n+1 revision。
- `generation_source="HISTORICAL_RESTORE"`。
- 保存 `source_revision`、原因、actor 和 command 证据。
- 运行现有 RULE + Reviewer Agent 审核。
- 新版本进入现有人工审核门禁，不自动批准。
- 不覆盖历史 Spec、PRD 文件或 Gitea commit。
- 新版 PRD 继续写入新的 `v{n+1}.md`。
- 同一 command ID 和完整请求必须幂等。

建议只在存在当前 Spec 且项目未进入不可变终态时开放；具体合法状态写入后端 `ACTION_TABLE`，前端只读取 `legal_actions`。

## 13. 前端页面调整

### 13.1 保留并真实接入

- Agent 对话：改为 Session 澄清与安全进度。
- PRD 弹窗：改为真实 Markdown 行、Gitea 评论、异步 publish 和人工 resolve。
- 看板：显示真实 WorkItem，第一阶段只读。
- DAG：由 dependency IDs 派生，只读。
- WorkItem 详情：显示后端事实字段、Agent Spec 和安全审计事件。

### 13.2 第一阶段隐藏或明确标记未启用

- Code Editor。
- Git Commits。
- 部署、测试、漏洞扫描和解除运行阻塞快捷动作。
- 模拟 stdout、随机 commit、随机任务和模拟执行延迟。
- PRD HEAD reset 式回滚。

### 13.3 Mock 模式

如需保留展示，可显式配置：

```env
VITE_DATA_MODE=mock
```

真实模式：

```env
VITE_DATA_MODE=api
```

两种模式必须在 UI 中清晰标识，禁止真实模式静默回退到 mock 数据。

## 14. 后端小型基础扩展

- `ActorResolver` 与本地/代理身份模式。
- 可配置 CORS 或同源部署支持。
- `GET /healthz`：只验证进程和数据库基础可用性，不访问 Agent/Gitea。
- `GET /prd/{wi}/commentable-lines`。
- `restore_spec_version` 命令及审核/幂等实现。
- 安全 `reasoning_summary` / `trace_events` 持久化与公开投影。
- 保留单 worker 约束，直到 ReviewTask 迁移到跨进程执行器。

## 15. 验收标准

1. API 模式启动后不加载 `INITIAL_*` mock 业务数据。
2. 浏览器请求中不包含可信 actor、Gitea token 或 Agent provider key。
3. 用户可创建 Session、回答或显式跳过澄清，并按照 `legal_actions` 完成 Spec 流程。
4. `STALE_STATE` 会刷新状态并要求用户确认，不静默重放写命令。
5. WorkItem 和 DAG 来自后端拆解结果，不包含虚构执行状态。
6. PRD 只在有效 diff 行允许批注；后端仍执行最终校验。
7. 多评论部分失败可被用户识别和重试。
8. publish 返回 202 后，刷新页面仍能继续查询原 task。
9. publish 完成后，版本、commit、评论和 Session 状态都从后端重新读取。
10. 评论不会因 Agent 回复而自动解决。
11. 历史恢复创建 n+1，并保留完整 parent/source/审核证据。
12. UI 不展示私有推理或受限诊断内容。
13. 前后端契约具备单元、集成和至少一条 fake-boundary E2E 覆盖。
14. 跳过澄清不会再次触发 PM 分析；PRD 生成输入包含 `AGENT_DISCRETION`，且产生 `CLARIFICATION_SKIPPED` 审计事件。
