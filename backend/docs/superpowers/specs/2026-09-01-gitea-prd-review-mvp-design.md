# Gitea PRD 批注评审 MVP 设计

**日期：** 2026-09-01  
**状态：** 用户已要求按增量任务书实施，待书面设计复核  
**基线：** `codex/project-spec-agent-spec-final-fixes@b57b837`，204 项测试通过  
**范围：** 在现有 firstFlight FastAPI、SQLite、Spec 审核和 Agent 体系上，新增 Gitea PRD 行内批注、回复、解决、发布下一版和任务查询；不做前端，不执行子 Agent。

## 1. 已确认约束

- 只做增量接入，不重写既有 Project、WorkItem、SpecVersion、CommandService、审核与 `/chat` 流程。
- 严格回合制。只有用户调用“生成下一版”才触发一次 PM Agent；不使用 Webhook、SSE、WebSocket、消息队列或批注监听。
- Gitea 中的 `docs/prd/{wi}/v{n}.md` 是对外 PRD 版本文件，Gitea review comment thread 是批注权威与审计源。
- SQLite 保存 Gitea 版本指针、批注索引和后台任务状态；现有 `SpecVersion` 继续承担 firstFlight 内部状态机、自动审核和批准门禁。
- Gitea 文件与本地 `SpecVersion.markdown` 必须使用相同 NFC/统一换行内容。`versions.content_hash` 保存该规范化 Markdown 的 SHA-256；`versions.spec_content_hash` 单独保存既有 `SpecVersion.content_hash`（结构化 ProjectSpecPayload SHA-256）作为桥接证据。两者不能混用；发现任一不一致时拒绝继续发布。
- 前端只使用后端契约，不接触 Gitea token。
- 评论、回复、PRD 内容和 Gitea 响应都是不可信输入，不能改变权限、工作流或系统提示词。

## 2. 方案比较

### 方案 A：另建独立 Gitea 评审状态机

实现最直接，但会绕过现有 `CommandService`、乐观并发和 `SpecReview`，形成两个相互冲突的 Spec 审批系统。拒绝。

### 方案 B：Gitea 评审桥接现有 Spec 工作流（采用）

新增独立 Gitea、PRD review 和 PM rewrite 组件；Gitea 管文件与批注，SQLite 管投影和任务；生成的新内容同时形成新的 `SpecVersion`，并进入现有自动审核，之后回到人工评审。该方案满足附件契约，也保留现有门禁。

### 方案 C：把全部 Spec 权威迁移到 Gitea

需要迁移现有 SpecVersion、审核回执、命令幂等、并发控制及查询接口，不符合“只新增、不重写”。拒绝。

## 3. 总体架构

```text
REST /prd 与 /tasks
  -> PrdReviewService
     -> GiteaClient（文件、PR、review comments、回复、resolve）
     -> SQLite versions/comments_index/tasks 投影
     -> PmRewriteService
        -> 现有 AgentGateway / PM Agent Session
        -> 现有 Spec 规则审核与 Reviewer Agent
        -> 现有 SpecVersion / SpecReview / AuditEvent
```

`PrdReviewService` 是新增端点唯一入口。它负责把根 WorkItem、Project、当前 SpecVersion、Gitea PR 和 SQLite 投影绑定起来，但无权直接批准 Spec 或创建 AgentSpec。

## 4. 标识与绑定

- 路径参数 `{wi}` 必须是现有项目的根 WorkItem ID；非根节点或不存在的 WorkItem 返回 `404`。
- 根 WorkItem 的 `project_id` 解析到当前 Project。
- `versions.wi + versions.version` 映射到一个现有 `SpecVersion.id` 和一个 Gitea 文件路径。
- 一个根 WorkItem 维护一个长期打开的 Gitea PR 作为评审会话。`versions.pr_number` 保存该 PR 编号，避免通过标题猜测。
- 第一次读取或创建批注前，系统必须已经有一个处于 `HUMAN_REVIEW` 或 `REWORK` 的当前 Spec；服务将其 Markdown 幂等发布为 `v{revision}.md` 并建立绑定。
- 已批准或已拆解的 Spec 仍可只读查看，但不能再新增批注或发布下一版。

## 5. SQLite 增量表

### 5.1 `versions`

```sql
CREATE TABLE versions (
  wi TEXT NOT NULL,
  version INTEGER NOT NULL,
  spec_version_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  pr_number INTEGER NOT NULL,
  commit_sha TEXT,
  content_hash TEXT NOT NULL,
  spec_content_hash TEXT NOT NULL,
  change_summary TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (wi, version),
  UNIQUE (spec_version_id)
);
```

该表是外部版本投影，不替代现有 `spec_versions` 表。旧库迁移会从对应 `spec_versions.markdown` 重算规范化 Markdown hash，并把旧的结构化 hash 保存在 `spec_content_hash`；迁移幂等且缺少对应 Spec 时失败关闭。

### 5.2 `comments_index`

```sql
CREATE TABLE comments_index (
  id INTEGER PRIMARY KEY,
  wi TEXT NOT NULL,
  pr_number INTEGER NOT NULL,
  path TEXT NOT NULL,
  line INTEGER NOT NULL,
  author_type TEXT NOT NULL,
  body TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT
);
```

该表只用于查询加速和任务快照。每次对外读取以 Gitea 结果刷新本地字段；本地记录不能凭自身把 Gitea 评论判为存在、已回复或已解决。

### 5.3 `tasks`

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  wi TEXT NOT NULL,
  initiator_actor_id TEXT NOT NULL,
  status TEXT NOT NULL,
  base_version INTEGER NOT NULL,
  base_commit_sha TEXT NOT NULL,
  comment_ids TEXT NOT NULL,
  comment_snapshot TEXT NOT NULL,
  comment_snapshot_hash TEXT NOT NULL,
  reply_receipts TEXT NOT NULL,
  new_version INTEGER,
  new_spec_version_id TEXT,
  new_commit_sha TEXT,
  error_code TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

对外状态保持 `pending | processing | done | error`。同一 `{wi, base_version, comment_snapshot_hash}` 的未完成任务只能有一个；重复发布返回已有 `task_id`，避免重复调用 Agent。任务固定保存发起者；执行和恢复前必须确认同一 actor 仍具有当前项目责任，命令与审计也使用该 actor。`reply_receipts` 保存 Gitea 返回的 reply ID、服务 user ID 和精确正文。

应用启动时，遗留 `pending` 或 `processing` 任务转为可解释的 `error`（`PROCESS_INTERRUPTED`）。本 MVP 没有持久任务队列，不伪装成自动恢复。任务执行和 `CommandService` 的进程内 ownership fence 都要求单进程、单 worker 部署；多进程不是本 MVP 的支持拓扑。

## 6. Gitea 适配

新增 `app/services/gitea.py`，封装：

- 首次 PRD 操作时懒执行并缓存能力预检；应用启动不访问 Gitea。最低版本为 Gitea 1.27.0，token 必须能读取身份、仓库和 base branch，并具有仓库 push/pull 与 PR review/reply/resolve 权限；
- 读取/创建 `docs/prd/{wi}/v{n}.md`；
- 获取或创建评审 PR；
- 使用 `GET /branches/{branch}` 读取分支，使用 `POST /branches` 与 `{new_branch_name, old_ref_name}` 创建分支，响应只信任 `commit.id`；409 后重读；
- 使用受支持的 `base_branch` 参数分页列出 PR，并在本地精确匹配 head/base；分页读取 reviews 和 changed files；
- 列出 PR reviews 及其 comments，并按实际 `review + path + position` 组装 thread/replies；一个 review 可含多个独立根。Gitea 的 comment schema 没有 `in_reply_to_id`，实现不得依赖该字段；重复 ID、缺失 anchor 或跨 review 记录作为不兼容/歧义失败；
- 创建包含单个行内评论的 `COMMENT` review；
- 回复 review comment；
- resolve/unresolve review comment；
- 返回明确、可分类的认证、权限、限流、网络、版本不兼容和数据错误。

官方创建行内批注使用 `POST /repos/{owner}/{repo}/pulls/{index}/reviews`，回复使用 `/pulls/{index}/comments/{id}/replies`，resolve 使用 `/pulls/comments/{id}/resolve`。附件中的 issue comment `reply_to` 伪代码不作为实现合同。

API 接收的是源文件行号，Gitea 的 `new_position` 合同也是新文件源行号。适配器依据 PR changed-file patch 验证 `path + line` 确实是可评论的 context/addition 行，然后把原请求行号原样作为 `new_position`；它不是 hunk body offset。无法验证、删除行、越界或该行未出现在 patch 时返回 `422 COMMENT_LINE_NOT_IN_DIFF`。

HTTP 客户端设置连接和读取超时，隐藏 token，错误正文只进入受限日志；公开 API 只返回稳定错误码和安全消息。

## 7. 配置

在 `Settings` 和 `.env.example` 追加：

```text
GITEA_URL=
GITEA_TOKEN=
GITEA_OWNER=
GITEA_REPO=docs
GITEA_BASE_BRANCH=main
GITEA_REVIEW_BRANCH_PREFIX=prd-review/
GITEA_TIMEOUT_SECONDS=20
```

启动不主动访问外网。首次调用 PRD review 端点时执行配置和能力预检。缺失配置返回 `503 GITEA_NOT_CONFIGURED`。

## 8. API 契约

新增 `app/api/prd_review.py` 并在 `main.py` 追加路由注册：

```text
GET    /prd/{wi}
GET    /prd/{wi}/v/{n}
GET    /prd/{wi}/versions
GET    /prd/{wi}/comments
POST   /prd/{wi}/comments
POST   /prd/{wi}/comments/{id}/reply
POST   /prd/{wi}/comments/{id}/resolve
POST   /prd/{wi}/reviews/publish
GET    /tasks/{task_id}
```

请求体沿用附件字段，并追加 firstFlight 已有责任校验所需的 `actor_id`：

- 新增批注：`{actor_id, line, text, anchor?}`；`anchor` 只作展示校验，不能替代行号。
- 人工回复：`{actor_id, text, author_type: "human"}`；公开端点禁止伪造 `agent`。
- resolve：`{actor_id, resolved}`。
- publish：`{actor_id}`。

新增、回复、resolve 和 publish 只允许现有 final approver、project manager 或 root owner。评论正文是纯文本，拒绝空白、NUL 和超长输入；API 不生成 HTML。

`GET /tasks/{task_id}` 返回：

```json
{
  "task_id": "uuid",
  "wi": "root-work-item-id",
  "status": "done",
  "base_version": 1,
  "new_version": 2,
  "new_commit_sha": "...",
  "error": null
}
```

内部异常、token、命令行和 Gitea 响应正文不得出现在 `error`。

## 9. 发布下一版流程

### 9.1 请求阶段

`POST /prd/{wi}/reviews/publish` 在返回 `202` 前完成：

1. 解析根 WorkItem、Project 和当前版本绑定；
2. 校验当前 Spec 处于 `HUMAN_REVIEW` 或 `REWORK`，且 actor 有人工审核权限；
3. 从 Gitea 读取全部评论线程，筛选未解决的顶层批注；
4. 没有未解决批注则返回 `400 NO_UNRESOLVED_COMMENTS`；
5. 冻结 comment IDs、正文、行号、回复历史、base version、base commit SHA 和 snapshot hash；
6. 幂等创建 `pending` task，并通过 `BackgroundTasks` 安排一次执行；
7. 返回 `{task_id, base_version, comment_count}`。

请求完成后新增评论不会进入本次快照，留给下一轮。hash 的临时投影按 comment/reply ID 排序，避免 Gitea 纯展示重排产生新任务；持久快照与 Agent payload 保持显示顺序。失败任务恢复只使用持久 `comment_snapshot`，绝不重新读取 live comments 覆盖已 202 接受的证据。执行前发现 base commit 或当前 Spec 已变化时，任务失败为 `STALE_REVIEW_BASE`。

### 9.2 PM Agent 阶段

新增 `app/services/pm_agent.py`，复用现有 PM Agent Session 和 AgentGateway。输入包括：

- 当前结构化 Spec 与 Markdown；
- Project Brief、原始 Artifact 和澄清历史；
- 冻结的未解决批注及回复；
- 固定系统约束和输出 schema。

结构化输出为：

```json
{
  "spec": {"...": "完整 ProjectSpecPayload"},
  "responses": [
    {"comment_id": 123, "action": "MODIFIED", "note": "补充降级路径"}
  ],
  "change_summary": "..."
}
```

验证规则：

- `responses` 必须恰好覆盖快照中的全部顶层 comment IDs，不能缺失、重复或增加；
- action 只能为 `MODIFIED | CLARIFIED | NOT_ACCEPTED | NEEDS_HUMAN_CONFIRMATION`；
- 新 Spec 仍通过现有 ProjectSpecPayload 校验、规则审核和 Reviewer Agent 语义审核；
- 输入中的指令不得改变状态、权限或输出 schema；
- 不删除已通过内容，不编造指标，未知值明确写为待补充。

### 9.3 持久化和外部副作用顺序

1. 任务进入 `processing`。
2. PM Agent 生成并通过结构校验。
3. 使用现有 Spec 服务创建新的不可变 `SpecVersion` n+1 和自动审核回执；其父版本必须是冻结 base。成功的 publish 即使结构化内容相同也不能把 task 绑定回 parent。
4. 把同一 Markdown 幂等写入 Gitea `v{n+1}.md`，记录 commit SHA 和 content hash。
5. 写入 `versions` 投影；若相同版本已存在，必须验证路径、hash 和 SHA 一致。
6. 逐条回复快照中的批注。正文带服务 token HMAC 签名 marker；幂等恢复必须同时验证精确正文、签名、Gitea 返回 reply ID 和预检得到的服务 user ID。仅含相似 marker 的人工回复不能被过滤或当成回执。
7. 回复全部成功后任务置为 `done`，写入 `AuditEvent`。

外部系统无法与 SQLite 做一个事务。每一步必须以 task 记录为恢复证据并保持幂等：重试不得生成第二个 SpecVersion、第二个文件或重复回复。若 Gitea 写入或回复失败，任务为 `error`；已生成的本地版本和成功的外部副作用保留审计，不删除、不覆盖。MVP 通过同一发起者重新提交 publish，把原 task 置为 `pending` 并复用原 command receipt；恢复使用持久快照，不自动后台重试，也不创建第二个 task。

`NEEDS_HUMAN_CONFIRMATION` 或自动审核产生阻塞 finding 时，新版本进入现有 `NEED_CLARIFICATION` 或 `REWORK`，但已发布的 Gitea 文件仍保留。Agent 回复必须如实说明“待确认”或“未通过审核”，不能一律声称完成。

### 9.4 命令准备崩溃恢复

`command_attempts` 保存 `prepare_owner_id/prepare_owner_started_at`。单进程内的活跃 owner 阻止并发重复 prepare；进程崩溃或协程取消后，没有绑定 AgentCall 的 PREPARING 可安全重跑，绑定且已校验的 `RESULT_READY` 由 handler 复用，存在 `PENDING/AMBIGUOUS` 则保持 `COMMAND_IN_DOUBT`，因为无法证明外部调用是否越线。PREPARED/materialized 仍沿用原两阶段回执。

## 10. 与现有工作流的衔接

- 新增一个内部“comment revision”命令处理器，复用 `CommandService` 的命令尝试、状态版本、CAS 和审计能力；BackgroundTask 不能直接改 Project 受保护字段。
- `HUMAN_REVIEW`/`REWORK` 内部允许 coordinator 使用 `publish_review` 生成下一版；该内部命令不出现在 legacy Session `legal_actions/next_action` 公共工作流中。原有 `approve/reject/rework/revise` 行为不变。
- 生成的新版本自动进入现有 RULE + AGENT 自动审核；通过后回到 `HUMAN_REVIEW`。
- 只有 `APPROVED` Spec 才能 `convert_to_work_item`，本功能不执行子 Agent。
- 黑板或通知若当前代码没有稳定接口，只记录 `AuditEvent`，不新增空壳并行机制。

## 11. 错误与状态

- `400`：没有未解决批注、当前状态不允许发布。
- `403`：actor 无人工审核责任。
- `404`：WorkItem、版本、评论、task 或 Gitea PR 不存在。
- `409`：版本/commit 已变化、重复请求内容冲突、版本投影 hash 冲突。
- `422`：空白评论、非法行号、行不在 diff、Agent 输出不完整。
- `503`：Gitea 未配置、不可达、不兼容或 Agent 暂不可用。

任务错误保存稳定 `error_code`；公开 `error` 是安全摘要。错误不会自动 resolve 评论、批准 Spec 或推进到 AgentSpec。

## 12. 测试策略

所有实现遵循失败测试先行。使用真实 service/domain 代码、隔离 SQLite、FakeAgent 和 fake `httpx` transport；不访问真实 Gitea、LLM 或公网。

### 单元测试

- Gitea URL、认证头、超时和错误分类；
- diff line → position 映射，包括新增/上下文/删除/越界；
- 评论 thread/replies/resolver 投影；
- PM 输出 comment ID 精确覆盖和 action 校验；
- 内容 hash、版本路径和回复文本；
- 纯文本长度与控制字符校验。

### 集成测试

- 三张表初始化与旧数据库增量升级；
- 根 WorkItem 解析、责任校验和状态门禁；
- v1 幂等发布与 Gitea/Spec hash 一致；
- 评论 CRUD 实际调用 fake Gitea 并刷新索引；
- publish 返回 202，任务 `pending → processing → done/error`；
- 无批注不调用 Agent；
- stale base 不调用 Agent；
- 批注快照不吸收发布后的新评论；
- Gitea 文件写失败、部分回复失败和重试不重复生成版本；
- 自动审核 REWORK/NEED_CLARIFICATION 路径；
- 原有 approve、revise、convert 和 `/chat` 行为不变。

### 完整闭环

Fake Gitea + FakeAgent 覆盖：加载 v1 → 两条行内批注 → publish → 生成 v2 → 两条逐项回复 → 查询 done → v2 进入既有人工审核门。

## 13. 文件边界

主要新增：

- `app/api/prd_review.py`
- `app/services/gitea.py`
- `app/services/prd_review.py`
- `app/services/pm_agent.py`
- `app/schemas/prd_review.py`
- `prompts/nodes/pm_rewrite_prd.txt`
- `tests/unit/test_gitea_service.py`
- `tests/integration/test_prd_review.py`
- `.env.example`

仅追加：

- `main.py`：依赖构造与路由注册；
- `app/config.py`：Gitea 配置；
- `app/database/models.py`：三张表；
- `app/domain/types.py`：结构化 PM rewrite 输出；
- `app/agents/gateway.py` 与 `app/agents/codex.py`：一个 rewrite 能力；
- `app/domain/workflow.py`、`app/services/command_service.py`、`app/services/spec_service.py`：受门禁的批注修订动作；
- `requirements.txt`：`httpx` 已存在，无重复依赖；若实现采用 subprocess git，则不增加 GitPython。

不修改或删除旧测试，不覆盖旧 Spec 文件，不提交 `.DS_Store`、`.superpowers/` 或凭据。

## 14. 验收

1. 九个新增端点符合本设计合同和 OpenAPI schema。
2. Gitea 文件、评论、回复与 resolve 均由 fake transport 证明调用正确官方路径。
3. 新版 Markdown hash 与结构化 Spec hash 分栏验证，旧版不被覆盖。
4. 每条快照批注恰好收到一条与真实处理结果一致的 Agent 回复。
5. 无批注、stale base、Agent/Gitea 失败均不越过现有审核门。
6. 任务状态和错误可查询，敏感诊断不外泄。
7. 新增闭环测试、完整回归、编译检查全部通过。
8. 没有 Webhook、轮询监听、SSE、WebSocket、消息队列、前端或子 Agent 执行路径。

