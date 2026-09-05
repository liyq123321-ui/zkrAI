# Project Brief → Agent Spec MVP

## 前端对接配置（第一阶段）

HTTP 边界已支持轻量可信身份与 React API 模式：

```env
FIRSTFLIGHT_IDENTITY_MODE=local
FIRSTFLIGHT_ACTOR_ID=owner-1
FIRSTFLIGHT_CORS_ORIGINS=http://127.0.0.1:3001,http://localhost:3001
```

- `local` 模式会忽略浏览器提交的 `actor_id`，统一代填 `FIRSTFLIGHT_ACTOR_ID`。
- `proxy` 模式读取 `FIRSTFLIGHT_TRUSTED_ACTOR_HEADER`（默认 `X-FirstFlight-Actor`），只能放在可信反向代理之后。
- `legacy` 仅为现有测试和旧客户端保留。
- `GET /healthz` 只检查进程和数据库，不访问 Agent/Gitea。
- `GET /sessions` 只读列出数据库中全部项目的 Session ID、项目 ID、Root WorkItem ID 与名称，供共享看板及主任务多选使用；不创建 Agent 运行。
- `GET /prd/{wi}/commentable-lines` 只返回 Gitea diff 的 context/addition 行。
- `restore_spec_version` 创建重新审核的 n+1 Spec，不覆盖历史。
- `/sessions/{id}/events` 公开安全阶段摘要与哈希，不公开私有原始推理或受限诊断信息。

生成的接口快照位于 `../docs/openapi.json`。

这是一个受状态机约束的项目规划 API：人类提交 `Project Brief`，PM Agent 先澄清需求，再生成带版本的 Project Spec；通过自动审核、Reviewer Agent 语义审核和人工审核后，系统才会把 Spec 拆成 WorkItem 与每个任务对应的 Agent Spec。

本 MVP 的明确终点是 `AGENT_SPECS_READY`：它只生成、保存并提供子 Agent 的结构化 Spec，**不会启动、分派或执行任何子 Agent**。

## 本地运行

需要 Python 3.12+、可用的 `codex` CLI（已登录）以及数据库写入权限。默认数据库为仓库内的 `data/gateway.db`；首次启动会创建或迁移表。

```bash
cd /Users/tangtang/Desktop/firstFlight
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8088
```

如已激活虚拟环境，启动命令等价于：

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8088
```

本 MVP 必须以**单进程、单 worker**运行（上面的 uvicorn 默认即为一个 worker）；不要设置 `--workers > 1`，也不要由多进程进程管理器复制应用。FastAPI BackgroundTasks 和命令准备 ownership fence 只在一个进程内协调，跨进程任务队列不在本 MVP 范围内。

可选环境变量如下；不设置时使用默认值。

```bash
export DATABASE_URL='<sqlite-database-url>'
export CODEX_BINARY='<codex-executable>'
export CODEX_RUNTIME_HOME='<codex-runtime-home>'
export CODEX_WORKING_DIRECTORY='<firstflight-working-directory>'
export CODEX_TIMEOUT_SECONDS='<seconds>'
export CODEX_INACTIVITY_TIMEOUT_SECONDS='<seconds>'
```

`CODEX_TIMEOUT_SECONDS` 限制一次 Codex 调用的总时长（默认 2,000 秒）；
`CODEX_INACTIVITY_TIMEOUT_SECONDS` 限制连续收不到 Codex JSONL 事件的时长（默认 600 秒）。
后者用于终止已经失去活动迹象的子进程，而正常输出的长任务仍可继续运行。

本服务没有单独的 health endpoint；可用 `POST /sessions` 或读取已有 Session 验证服务可用性。

## 核心约束

- `actor_id` 是持久化的责任/授权元数据，而非登录身份。本 MVP **没有认证或鉴权中间件**；调用方可以声明任意 `actor_id`，但工作流会按项目中保存的责任人限制澄清回复和人工审核。
- `final_approver`、`project_manager_ids`、`root_owner_ids` 中的成员可进行人工 `approve`、`reject`、`rework`；澄清 `message` 也只允许这些项目参与人。其他 actor 会得到 `403 FORBIDDEN_ACTOR`。
- 每次命令必须在 `expected_state_version` 中传入最新状态的 `state_version`。客户端应先读取命令响应中的 `state`，或 `GET /sessions/{session_id}/state`，再发下一条命令。
- 创建用的 `request_id` 在所有项目中全局唯一。使用同一完整创建请求重试不会新建项目，而是返回该项目**当时的当前 Session 状态**；并发的相同请求通过持久化 claim 共用一次 PM 分析；同一 `request_id` 搭配不同完整创建请求返回 `409 REQUEST_CONFLICT`。
- `command_id` 只需在同一个 Session 内唯一。仅已成功持久化的命令会从 `ProcessedCommand` 重放其原始 `CommandResult`；失败或被拒绝的尝试可以在动作仍合法、请求和可复用 Agent 证据仍匹配时以同一 ID 安全重试。相同 Session 内同一 `command_id` 搭配不同完整命令请求返回 `409 COMMAND_CONFLICT`。
- 命令崩溃恢复区分持久检查点：`PREPARING` 且尚无 AgentCall 可安全重跑；已有 `RESULT_READY` 会由 handler 复用；`PENDING/AMBIGUOUS` 因无法证明外部调用是否越线而返回 `COMMAND_IN_DOUBT`。活动的同进程 owner 不会被并发请求抢占。
- `expected_state_version` 是乐观并发控制字段。版本过期返回 `409 STALE_STATE`；重新读取状态后，使用新的命令 ID 和最新的 `expected_state_version` 重试。
- 所有业务错误采用 `{"detail":{"code":"…","message":"…"}}`。常见代码包括 `ILLEGAL_ACTION`、`FORBIDDEN_ACTOR`、`STALE_STATE`、`VALIDATION_ERROR`、`AGENT_UNAVAILABLE`、`INVALID_AGENT_RESULT` 和 `NOT_FOUND`。

## Gitea PRD 评审 API

该可选功能将处于人工评审或返工中的根 WorkItem 的当前 Spec 发布到长期打开的 Gitea PR，并围绕该 PR 的行内批注生成下一版 Spec。先配置 Gitea，再启动服务；所有示例值均为占位符，token 只保留在服务端环境中。

```bash
export GITEA_URL='<gitea-server-url>'
export GITEA_TOKEN='<gitea-access-token>'
export GITEA_OWNER='<gitea-owner>'
export GITEA_REPO='<gitea-repository>'
export GITEA_BASE_BRANCH='<gitea-base-branch>'
export GITEA_REVIEW_BRANCH_PREFIX='<gitea-review-branch-prefix>'
export GITEA_TIMEOUT_SECONDS='<seconds>'
```

目标服务最低为 **Gitea 1.27.0**。token 必须能读取当前用户、目标仓库和 base branch，具备仓库 pull/push，以及 PR review、review comment reply 和 resolve/unresolve 所需权限。服务启动时不会访问 Gitea；首次 PRD 操作才懒执行并缓存非写入能力预检。分支使用官方 `GET /repos/{owner}/{repo}/branches/{branch}` 与 `POST /repos/{owner}/{repo}/branches`（`new_branch_name`/`old_ref_name`，响应 `commit.id`）；PR、reviews 和 changed files 会分页读取。缺少配置返回 `503 GITEA_NOT_CONFIGURED`；资源缺失与 API 不兼容分别分类，不会把 token、响应正文、命令行或内部异常返回给调用方。

Gitea 中的 `docs/prd/{wi}/v{n}.md` 和 Gitea review comment thread 是 PRD 文件、批注和外部审计的权威来源。SQLite 只保存版本、批注索引和发布任务的投影/恢复证据。`versions.content_hash` 是 NFC/统一换行后的 Markdown SHA-256；`versions.spec_content_hash` 独立保存结构化 `SpecVersion.content_hash`，迁移旧库时分别重算/保留。两条桥接证据或 Gitea 内容不一致都会拒绝继续发布。

`{wi}` 必须是根 WorkItem ID。写操作要求既有的 final approver、project manager 或 root owner；评论正文是纯文本。以下九个端点构成完整评审契约：

| Endpoint | 作用 |
| --- | --- |
| `GET /prd/{wi}` | 获取当前已绑定 PRD 版本。 |
| `GET /prd/{wi}/v/{number}` | 获取指定 PRD 版本。 |
| `GET /prd/{wi}/versions` | 列出已绑定的 PRD 版本。 |
| `GET /prd/{wi}/comments` | 从 Gitea 刷新并读取评论线程。 |
| `POST /prd/{wi}/comments` | 新增行内评论：`actor_id`、`line`、`text` 和可选 `anchor`。 |
| `POST /prd/{wi}/comments/{comment_id}/reply` | 人工回复：`actor_id`、`text`、`author_type: "human"`。 |
| `POST /prd/{wi}/comments/{comment_id}/resolve` | 解决或恢复评论：`actor_id`、`resolved`。 |
| `POST /prd/{wi}/reviews/publish` | 冻结未解决评论并接受一次下一版发布任务：`actor_id`。 |
| `GET /tasks/{task_id}` | 查询发布任务的 `pending`、`processing`、`done` 或 `error` 状态。 |

新建评论的 `line` 是 PRD 新文件源行号；Gitea 的 `new_position` 也要求这个源行号，而不是 hunk 内偏移。服务先用分页读取的 changed-file patch 验证该 `path + line` 是 context/addition 行，再把请求行号原样发送。删除行、越界、未展示行或歧义返回 `422 COMMENT_LINE_NOT_IN_DIFF`，不会改用相邻行。

一个 Gitea review 可以包含多个独立根线程。服务按实际 `review + path + position` 分组；Gitea reply schema 没有 `in_reply_to_id`，实现不依赖该字段。Agent 回复的 `author_type` 只有在本地持久 reply ID、预检服务 user ID、精确正文和 token-HMAC 签名 marker 都与 live Gitea 回复匹配时才为 `agent`；人工复制 marker-looking 文本仍是 `human`。

```bash
export FIRSTFLIGHT_URL='<firstflight-api-base-url>'
export WI='<root-work-item-id>'
export ACTOR_ID='<authorized-actor-id>'
export COMMENT_ID='<gitea-review-comment-id>'
export TASK_ID='<review-publication-task-id>'

# 新增一条行内批注；成功返回 204。
curl -sS -X POST "$FIRSTFLIGHT_URL/prd/$WI/comments" \
  -H 'Content-Type: application/json' \
  -d '{"actor_id":"'"$ACTOR_ID"'","line":2,"text":"请补充责任人。"}'

# 发布冻结的未解决批注；成功返回 202、task_id、base_version、comment_count。
curl -sS -X POST "$FIRSTFLIGHT_URL/prd/$WI/reviews/publish" \
  -H 'Content-Type: application/json' \
  -d '{"actor_id":"'"$ACTOR_ID"'"}'

# 查询任务。仅当 status 为 done 时，new_version 和 new_commit_sha 才表示完成的发布。
curl -sS "$FIRSTFLIGHT_URL/tasks/$TASK_ID"
```

发布请求在返回 `202` 前冻结未解决的顶层评论、回复、base version、base commit、发起 actor 与快照；hash 排序不改变保存/传给 Agent 的显示顺序。后续 live 评论或纯重排属于下一轮，失败任务恢复只使用已接受的持久快照，不回读 live comments 覆盖它。只有原发起 actor 可恢复，且执行任何 Agent/Gitea 副作用前会重新验证该 actor 仍有当前责任。进程中断时启动仅把遗留任务标为 `PROCESS_INTERRUPTED`，不会自动重试。

每次成功 publish 都创建不可变 n+1，不能把 task 绑定回 parent，即使 PM 决定不修改结构化内容。`NEEDS_HUMAN_CONFIRMATION` 至少进入 `NEED_CLARIFICATION`（结构/语义失败时可为 `REWORK`），不会自动通过。逐条回复保存 Gitea 返回的 reply ID/服务 user ID/精确签名正文；部分失败重试据此避免重复副作用。

验证说明：PRD review 聚焦测试通过 fake HTTP transport 与 FakeAgent/`ScriptedAgentGateway` 边界验证服务行为；测试不会调用真实 Gitea、启动 Codex 子进程或访问网络。

Gitea publish 生成的新版本仍会进入既有 RULE + AGENT 自动审核，并回到既有人工审核门禁。内部 `publish_review` 只供 PRD coordinator 使用，不出现在 legacy Session 的公开 `legal_actions/next_action`。人工 `approve`、`reject`、`rework` 和已批准版本的 `convert_to_work_item` 仍只能使用本 README 中的 Session command API；PRD 评审端点不会批准 Spec、转换 WorkItem 或执行子 Agent。

## 创建项目 Session

`POST /sessions` 会创建 Project、根 WorkItem、PM Agent Session 和原始需求交付物，并立即请求 PM Agent 做澄清分析。若分析尚缺信息，状态为 `NEED_CLARIFICATION`；若已充分，状态为 `SPECIFICATION`。

```bash
curl -sS -X POST http://127.0.0.1:8088/sessions \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "project-20260901-001",
    "actor_id": "approver-1",
    "brief": {
      "motivation": "需要把项目需求稳定地转为可审核的子 Agent Spec",
      "final_objective": "生成已审核的 WorkItem 和 Agent Spec",
      "known_scope": ["项目澄清", "Spec 审核", "任务拆解"],
      "exclusions": ["执行子 Agent", "生产环境部署"],
      "reference_materials": ["prd-v1", "architecture-note"],
      "expected_deliverables": ["已批准的 Project Spec", "WorkItem", "Agent Spec"],
      "time_constraints": "本季度完成 MVP",
      "staffing_constraints": "一个后端团队",
      "final_approver": "approver-1",
      "project_manager_ids": ["pm-1"],
      "root_owner_ids": ["owner-1"]
    }
  }'
```

成功返回 `201`，主体是当前状态；字段中的 `legal_actions` 是唯一应向用户开放的下一步命令。

```json
{
  "session_id": "…",
  "project_id": "…",
  "phase": "NEED_CLARIFICATION",
  "state_version": 1,
  "current_spec_version_id": null,
  "current_spec_status": null,
  "legal_actions": ["message"],
  "next_action": "ANSWER_CLARIFICATION",
  "outstanding_questions": [{
    "question_id": "Q1",
    "question": "谁负责最终业务确认？",
    "reason": "责任人影响验收与审核。",
    "affected_areas": ["permissions"],
    "blocking": true
  }],
  "review_findings": []
}
```

## 命令 API 和状态门禁

所有状态改变均通过：

```text
POST /sessions/{session_id}/commands
```

每个请求都包含以下共同字段；`payload` 目前保留为扩展对象，合法示例均传 `{}`。

```json
{
  "command_id": "在当前 Session 内唯一且稳定的客户端命令 ID",
  "action": "下列动作之一",
  "expected_state_version": 1,
  "actor_id": "项目责任人 ID",
  "payload": {}
}
```

命令成功返回：

```json
{
  "command_id": "…",
  "state": { "session_id": "…", "phase": "…", "state_version": 2 },
  "created_resource_ids": ["本次动作的主资源 ID"]
}
```

`created_resource_ids` 不是该事务所有插入行的清单，而是动作的主资源：人工 `approve`/`reject`/`rework` 返回一个 Human SpecReview ID；`message` 返回 ClarificationResponse ID，若 PM 仍要求澄清则还返回新 ClarificationRequest ID；`skip_clarification` 返回记录用户主动接受模糊性的 ClarificationResponse ID；首次 `create_spec` 和内容实际改变的 `revise` 返回新 SpecVersion ID；自动审核恢复和无变更 `revise` 返回空数组；`convert_to_work_item` 只返回 AgentSpec IDs，不返回 WorkItem 或依赖 ID。

`state.phase`、`state.current_spec_status` 和 `state.legal_actions` 必须以服务返回值为准。以下是全部合法动作、其精确前置状态和请求体。

### 1. 回答澄清：`message`

仅在 `NEED_CLARIFICATION`（没有当前 Spec）或 `REVIEW + NEED_CLARIFICATION`（当前 Spec 需要补充）时合法。`message` 必填且非空。PM Agent 会重新分析；结果可能继续提问、进入 `SPECIFICATION`，或在 Spec 澄清后回到 `REVIEW + REWORK`。

```json
{
  "command_id": "answer-q1",
  "action": "message",
  "expected_state_version": 1,
  "actor_id": "approver-1",
  "message": "最终业务确认由 approver-1 负责。",
  "payload": {}
}
```

### 1.1 跳过初始澄清：`skip_clarification`

仅在 `NEED_CLARIFICATION` 且尚无当前 Spec 时合法。系统不会再次调用 PM 分析，而是保存用户主动接受模糊性的审计证据，并进入 `SPECIFICATION`。后续 `create_spec` 会把 `AGENT_DISCRETION` 决策交给生成 Agent：Agent 使用保守、合理的假设补齐模糊地带，并在 PRD 中明确列出重要假设供人工批改。

```json
{
  "command_id": "skip-q1",
  "action": "skip_clarification",
  "expected_state_version": 1,
  "actor_id": "approver-1",
  "payload": {}
}
```

WebGUI 会在该命令成功后自动调用 `create_spec`，因此用户只需点击一次“跳过澄清并生成 PRD”。澄清框中的“跳过澄清”“我希望跳过澄清阶段”“不用澄清”“直接生成 PRD”等少量明确短语也会映射为同一命令；普通回答仍按 `message` 提交。若生成失败，状态会安全停留在 `SPECIFICATION`，可使用新的 command ID 重试 `create_spec`。

### 2. 生成或恢复自动审核：`create_spec`

仅在 `SPECIFICATION`（尚无当前 Spec）或 `REVIEW + AUTO_REVIEW`（先前自动审核被中断、可恢复）时合法。首次生成会建立不可变的 Spec 版本；恢复自动审核不会新建版本。`AUTO_REVIEW` 不是一次成功合并审核后的业务结果，而是可恢复的中间状态。

```json
{
  "command_id": "create-spec-v1",
  "action": "create_spec",
  "expected_state_version": 2,
  "actor_id": "pm-1",
  "payload": {}
}
```

生成后的自动审核由两层组成：确定性结构化规则（完整性、可验证验收、来源、边界和责任等）以及独立的 Reviewer Agent 语义审核。结构性规则不足以安全审核的输入不会交给 Reviewer；其余 Spec 必须保留 Reviewer 的审核证据。结果会进入：

- `REVIEW + HUMAN_REVIEW`：可等待人工审核；
- `REVIEW + REWORK`：规则或 Reviewer 发现阻塞问题；
- `REVIEW + NEED_CLARIFICATION`：需要人类补充。

### 3. 人工通过：`approve`

仅在 `REVIEW + HUMAN_REVIEW` 合法，且 actor 必须是保存的审核责任人。可选的 `message` 会作为人工审核评论保存。通过后为 `REVIEW + APPROVED`，才允许拆解。

```json
{
  "command_id": "approve-spec-v1",
  "action": "approve",
  "expected_state_version": 3,
  "actor_id": "approver-1",
  "payload": {}
}
```

### 4. 人工驳回：`reject`

仅在 `REVIEW + HUMAN_REVIEW` 合法，且 actor 必须是保存的审核责任人。可选的 `message` 会作为审核评论保存。驳回后状态为 `REVIEW + REJECTED`，没有可用工作流动作。

```json
{
  "command_id": "reject-spec-v1",
  "action": "reject",
  "expected_state_version": 3,
  "actor_id": "approver-1",
  "message": "验收范围需要重新界定。",
  "payload": {}
}
```

### 5. 要求返工：`rework`

仅在 `REVIEW + HUMAN_REVIEW` 合法，且 actor 必须是保存的审核责任人。`message` 必填，作为返工意见。结果为 `REVIEW + REWORK`。

```json
{
  "command_id": "rework-spec-v1",
  "action": "rework",
  "expected_state_version": 3,
  "actor_id": "approver-1",
  "message": "补充异常流程及其可测试验收标准。",
  "payload": {}
}
```

### 6. 生成新 Spec 版本：`revise`

仅在 `REVIEW + REWORK` 合法。`message` 必填，作为新版本的修改摘要；系统保留父版本与修改记录，再进行同样的自动审核和 Reviewer Agent 语义审核。若生成的规范化内容与当前版本相同，系统不会制造新版本，`created_resource_ids` 为 `[]`，状态保持 `REVIEW + REWORK`；调用方必须补充实际修改后的输入或意见后再试。

```json
{
  "command_id": "revise-spec-v2",
  "action": "revise",
  "expected_state_version": 4,
  "actor_id": "pm-1",
  "message": "已补充异常流程与验收标准。",
  "payload": {}
}
```

### 7. 从已批准 Spec 拆解：`convert_to_work_item`

仅在 `REVIEW + APPROVED` 合法。拆解按三个持久阶段执行：PM Agent 先生成不含实施计划的基础任务树；系统验证任务层级、依赖、来源、必填输出和验收映射后，以最多两个并发调用逐任务生成 `implementation_plan`；最后 Reviewer Agent 审核组装后的完整拆解（包括排除项是否被违反）。所有验证和 Reviewer 都通过后，才在一个事务中持久化 WorkItem、依赖和 Agent Spec。

拆解是异步命令：服务在持久化 command job 后立即返回 `202 Accepted`，而不是等待 Agent 完成。提交响应包含稳定的 `command_id`、`status`、`status_url` 与 `events_url`，并把 `Location` 响应头设为同一个 `status_url`。异步拆解的 command ID 必须是 1–255 个 URL-safe ASCII 字符（字母、数字、`.`、`_`、`~`、`-`），这样返回的状态和事件路径可以直接使用；其余同步命令保留原有 command ID 兼容性与普通的 `200` CommandResult。同一 Session 同一时刻只允许一个未完成的拆解 job；即使重复提交使用了不同 command ID，服务也会返回已有活动 job，不会并行调用模型。

```bash
curl -i -X POST http://127.0.0.1:8088/sessions/SESSION_ID/commands \
  -H 'Content-Type: application/json' \
  -d '{"command_id":"COMMAND_ID","action":"convert_to_work_item","expected_state_version":7,"payload":{}}'

curl http://127.0.0.1:8088/sessions/SESSION_ID/commands/COMMAND_ID

curl -N http://127.0.0.1:8088/sessions/SESSION_ID/commands/COMMAND_ID/events
```

`GET .../commands/COMMAND_ID` 返回持久状态快照（`pending`、`processing`、`succeeded` 或 `failed`），并包含 `progress_stage`、`progress_message`、`last_activity_at`。逻辑阶段包括基础拆解、逐任务计划、语义审核、定向修复和准备落库；Codex 子进程的启动、推理、工作与完成事件也会更新活动时间和安全的中文进度文案，原始模型内容不会暴露到公开 API。

SSE 是实时通知通道，客户端不能只依赖它；随附前端在页面恢复已保存的 job 时先立即读取一次状态，之后以非重叠请求每 5,000 ms 轮询该状态端点，使 SSE 断连或遗漏帧时仍能取得终态。重启进程会使原有的内存 runner 丢失，启动迁移会补齐旧数据库的进度字段、把未完成的 job 标记为 `PROCESS_INTERRUPTED`，并建立 Session 级活动 job 唯一约束；此后可使用完全相同的 command ID 重新提交，服务会重新调度该持久 job。

SSE 成功响应的 `Content-Type` 是 `text/event-stream`。每个状态帧都采用 `event: command.status`，其 `id` 是该 job 单调递增的 `status_version`，`data` 是完整的 JSON 状态快照，结构与状态查询响应相同。浏览器重连时应把最后收到的 `id` 放进 `Last-Event-ID`；服务将它视为游标，只在当前快照版本更高时发出该快照。该端点不保存逐版本事件历史，因此客户端始终应以最新快照为准，而不是假定可以补回每个中间状态。

任务尚未终结而暂时没有新状态时，流约每 15 秒发送 `: keep-alive` 注释心跳；心跳没有事件 ID 或数据，不能替代状态读取。`succeeded` 或 `failed` 的最终快照发出后，流会关闭。短间隔轮询和 SSE 应并行使用：轮询提供断线期间与重连后的持久状态保证，SSE 只用于降低可见状态变化的延迟。

基础拆解和每个已通过验证的任务计划都会作为可恢复检查点保留。显式重试时，服务只复用与当前拆分契约版本、同一项目 UUID、已批准 Spec UUID/内容哈希、输入引用、基础调用 UUID 和任务内容哈希完全匹配的结果，并为当前命令创建新的采用证据；不会按项目名、任务名或裸 `FR-*` 标识查找。schema 或提示契约升级后，无版本或旧版本检查点自动失效，防止历史脏结果进入新流程。某个任务规划失败不会写入半成品业务行，重试只补缺失任务。最终审核若仅指出 `implementation_plan` 路径，会只重做对应任务计划；任务边界问题仍回到基础拆解修订。Spec 发生变化时，旧检查点也自动失效。

```json
{
  "command_id": "decompose-spec-v1",
  "action": "convert_to_work_item",
  "expected_state_version": 4,
  "actor_id": "pm-1",
  "payload": {}
}
```

成功后的终态：

```json
{
  "phase": "AGENT_SPECS_READY",
  "current_spec_status": "APPROVED",
  "legal_actions": [],
  "next_action": "NONE"
}
```

这不是子 Agent 的执行许可；服务在此停止。

## 读取工作流结果

所有查询均使用 Session ID，返回当前项目范围内的资源。未知 ID 返回 `404 NOT_FOUND`。

| Endpoint | 内容 |
| --- | --- |
| `GET /sessions/{session_id}/state` | 当前阶段、版本、合法动作、待答问题和当前 Spec 的审核发现。没有单独的 questions endpoint。 |
| `GET /sessions/{session_id}/specs` | 所有不可变 Spec 版本（按 revision 排序），每个版本内嵌审核记录。 |
| `GET /sessions/{session_id}/specs/{version}` | 一个 Spec；`version` 可为数字 revision 或版本 ID。 |
| `GET /sessions/{session_id}/work-items` | 根节点、里程碑、任务及每项的依赖 ID。 |
| `GET /sessions/{session_id}/work-items/{work_item_id}` | 一个 WorkItem 和 `dependency_work_item_ids`。 |
| `GET /sessions/{session_id}/agent-specs` | 所有已生成的 Agent Spec。 |
| `GET /sessions/{session_id}/agent-specs/{agent_spec_id}` | 一个 Agent Spec。 |
| `GET /sessions/{session_id}/events` | 审计事件，按创建时间排序。 |

公开审计事件只包含稳定错误码和安全消息；Agent stderr、原始无效输出、本地路径等诊断详情仅保留在受限的 AgentCall 记录中，不会由 `/events` 返回。

例如：

```bash
curl -sS http://127.0.0.1:8088/sessions/$SESSION_ID/state
curl -sS http://127.0.0.1:8088/sessions/$SESSION_ID/specs
curl -sS http://127.0.0.1:8088/sessions/$SESSION_ID/work-items
curl -sS http://127.0.0.1:8088/sessions/$SESSION_ID/agent-specs
curl -sS http://127.0.0.1:8088/sessions/$SESSION_ID/events
```

待澄清问题来自 state：

```json
{
  "outstanding_questions": [
    {"question_id":"Q1","question":"…","reason":"…","affected_areas":["…"],"blocking":true}
  ]
}
```

Spec 版本和审核记录的代表性字段如下；`reviews` 同时包含 `RULE`、`AGENT` 和 `HUMAN` 审核（实际存在的类型取决于当前流程）。

```json
{
  "id": "spec-version-id",
  "revision": 1,
  "content": {
    "background_and_goals": ["…"],
    "functional_requirements": [{
      "requirement_id": "FR-001",
      "statement": "系统必须创建工作流 Session。",
      "priority": "MUST"
    }]
  },
  "markdown": "# Project Spec…",
  "generation_source": "PM_AGENT",
  "input_refs": ["artifact:…"],
  "generator_agent_session_id": "…",
  "generator_call_id": "…",
  "parent_version_id": null,
  "change_summary": "Initial specification",
  "content_hash": "…",
  "status": "APPROVED",
  "reviews": [{
    "id": "review-id",
    "kind": "AGENT",
    "reviewer_id": "reviewer-session-id",
    "input_spec_hash": "…",
    "verdict": "PASS",
    "findings": [],
    "comments": null,
    "command_id": null,
    "created_at": "2026-09-01T00:00:00Z"
  }],
  "created_at": "2026-09-01T00:00:00Z"
}
```

WorkItem 的依赖以 ID 明确表达；根和里程碑没有 Agent Spec，只有 `TASK` 会有一个对应 Spec。

```json
{
  "id": "task-id",
  "project_id": "project-id",
  "parent_id": "milestone-id",
  "local_key": "t-api",
  "kind": "TASK",
  "executable": true,
  "title": "实现 Session API",
  "objective": "提供受工作流保护的 API",
  "status": "todo",
  "dependency_work_item_ids": ["t-domain-id"]
}
```

Agent Spec 是供后续调度器消费的结构化任务包，不是执行记录。

每份新生成的 Spec 还包含 `requirements`（从批准 PRD 复制的 requirement_id、statement、priority）和 `implementation_plan`。后者包括实现概述、接口适用说明、接口定义与输入输出字段类型/必填项/校验和异常行为、数据结构与验证规则、已确认或建议的设计选择，以及顺序实施步骤。步骤必须关联该任务的全部验收需求，明确具体方法、产出和验证方式；程序检查覆盖和标识，Reviewer 检查可执行程度与跨任务接口一致性。

历史 Spec 保持可读，缺少实现方案时页面明确提示。项目负责人可通过本机命令补齐已有任务（所有计划生成并通过复审后原子保存，保留原 ID、依赖、范围和批准 PRD）：

```sh
backend/.venv/bin/python backend/scripts/enrich_agent_specs.py --session-id '<session-id>' --actor-id '<project-owner-id>'
```

若自动修订后仍存在跨任务契约冲突，可整理完整的 `task_key -> ImplementationPlan` JSON 校正稿，再由系统独立审核后保存：

```sh
backend/.venv/bin/python backend/scripts/enrich_agent_specs.py --session-id '<session-id>' --actor-id '<project-owner-id>' --revised-plans '/absolute/path/plans.json' --source-review-call-id '<rejected-review-call-id>'
```

来源审核必须绑定当前未变化的 PRD、任务和权限快照，且不能包含待人工决定的问题。此入口只替换实施方案，不改任务范围或依赖，不重置自动修订轮次；完整候选及来源审核记录会一起提交给 Reviewer，只有审核通过才能原子保存。

接口和实现选择超出 PRD 已明确的技术细节时，作为 `PROPOSED` 建议记录；标为 `FIXED` 的决定必须有批准 PRD 依据。详细实现规格本身不代表代码已运行、方案已被额外批准或对应 Skill 已安装。

```json
{
  "id": "agent-spec-id",
  "project_id": "project-id",
  "work_item_id": "task-id",
  "source_spec_version_id": "spec-version-id",
  "dependency_work_item_ids": ["t-domain-id"],
  "content": {
    "objective": "…",
    "scope": ["…"],
    "exclusions": ["…"],
    "inputs": ["…"],
    "outputs": [{"name":"…","format":"…","required":true}],
    "acceptance_criteria": [{
      "requirement_ids": ["FR-001"],
      "criterion": "…",
      "verification_method": "…",
      "expected_result": "…"
    }],
    "required_skills": ["…"],
    "allowed_tools": ["…"],
    "allowed_paths": ["…"]
  },
  "content_hash": "…",
  "created_at": "2026-09-01T00:00:00Z"
}
```

事件可用于审计创建、命令、审核和失败证据：

```json
{
  "id": "event-id",
  "project_id": "project-id",
  "session_id": "session-id",
  "event_type": "COMMAND_APPLIED",
  "actor_id": "pm-1",
  "payload": {
    "command_id": "create-spec-v1",
    "action": "create_spec",
    "prior_state_version": 2,
    "new_state_version": 3,
    "agent_call_ids": ["…"]
  },
  "created_at": "2026-09-01T00:00:00Z"
}
```

## 兼容入口：`POST /chat`

旧入口仍保留，但只是 Session API 的安全兼容层，不能绕过任何门禁。

```bash
curl -i -N -X POST http://127.0.0.1:8088/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"设计一个用户登录系统", "actor_id":"legacy-user"}'
```

- 新对话会由 `message` 填充为最小 Project Brief，创建 Session，并在响应头 `X-Session-ID` 返回 ID；默认 `actor_id` 是 `legacy-user`。
- 后续对话通过 `X-Session-ID` 请求头或请求体 `session_id` 指向同一 Session；两者同时提供且不一致时返回 `422 VALIDATION_ERROR`。
- 响应是 `text/event-stream`，可出现 `session.created`、`clarification.requested`、`spec.ready`、`workflow.error` 和最终的 `next_action`。
- 它只能在 `message` 为合法动作的澄清阶段提交回复；例如已经进入 `SPECIFICATION` 时，继续发 chat 不会自动生成 Spec 或任务，而会发出 `workflow.error`（`ILLEGAL_ACTION`）。
- `workflow` 与 `agent` 字段可被旧客户端继续提交，但当前兼容层不以它们改变工作流行为。

## 验证

```bash
.venv/bin/pytest -q
PYTHONPYCACHEPREFIX=/tmp/firstflight-pyc .venv/bin/python -m compileall -q app tests main.py
git diff --check
```

完整端到端覆盖从 Project Brief、澄清、Spec 审核、人工批准到 `AGENT_SPECS_READY`，并确认不会执行子 Agent。
