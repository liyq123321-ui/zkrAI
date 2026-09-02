# firstFlight 前后端对接任务清单（第一阶段）

## 1. 执行原则

- 先建立契约测试，再替换 mock。
- 后端工作流状态是唯一权威。
- 每个阶段完成后都应保持可运行、可回滚。
- 不在同一批次中同时引入身份系统、执行平台和 UI 重构。
- 真实模式不得静默使用 mock 数据。

## 2. Phase 0：基线与契约冻结

### 后端

- [ ] 导出或快照当前 FastAPI OpenAPI schema。
- [ ] 为现有 `/sessions`、`/chat`、`/prd`、`/tasks` 路由补充契约回归测试。
- [ ] 确认开发启动端口、数据库路径和单 worker 约束。
- [ ] 确认 AgentGateway 和 Gitea 的开发替身配置。

### 前端

- [ ] 增加 `VITE_DATA_MODE=mock|api`。
- [ ] 增加 `VITE_API_BASE_URL`。
- [ ] 为现有 UI 类型与后端 DTO 建立差异清单。
- [ ] 增加 API 模式醒目标识，避免把 mock 当作真实结果。

### 验收

- [ ] mock 模式保持当前演示功能。
- [ ] api 模式尚未接线时明确报错，不回退 mock。
- [ ] OpenAPI 快照可用于后续契约变更审查。

## 3. Phase 1：传输层与轻量身份

### 后端

- [ ] 新增 `FIRSTFLIGHT_IDENTITY_MODE` 配置。
- [ ] 新增 `FIRSTFLIGHT_ACTOR_ID` 本地代填配置。
- [ ] 实现统一 `ActorResolver`。
- [ ] 调整写请求 HTTP 边界，使浏览器可以不提交 `actor_id`。
- [ ] 保持 Service 层显式传递 actor。
- [ ] 增加本地模式覆盖客户端 actor 的安全测试。
- [ ] 增加代理模式可信 header 的测试和部署说明。
- [ ] 增加最小 `GET /healthz`。
- [ ] 增加显式 CORS allowlist，或确认生产同源部署。

### 前端

- [ ] 创建 `src/api/client.ts`。
- [ ] 创建 `src/api/errors.ts`。
- [ ] 创建 `src/api/dto.ts`。
- [ ] 支持 JSON、204、202、请求取消和稳定错误结构。
- [ ] 所有写请求不得从浏览器注入可信 actor。
- [ ] 增加网络离线、超时、422、409、503 的 UI 状态。

### 测试

- [ ] API client 单元测试。
- [ ] FastAPI actor 注入集成测试。
- [ ] 浏览器请求不包含服务端机密的检查。
- [ ] health/CORS smoke test。

### 验收

- [ ] 本地用户无需登录即可由服务端稳定映射到固定 actor。
- [ ] 修改浏览器请求体无法冒充其他 actor。
- [ ] 错误结构在所有页面统一呈现。

## 4. Phase 2：Session、Spec 与审核闭环

### 前端 API

- [ ] 实现 `createSession`。
- [ ] 实现 `getSessionState`。
- [ ] 实现 `executeCommand`。
- [ ] 实现 `listSpecs/getSpec`。
- [ ] 实现 `listWorkItems/getWorkItem`。
- [ ] 实现 `listAgentSpecs/getAgentSpec`。
- [ ] 实现 `listEvents`。
- [ ] 实现 `/chat` SSE 解析器。

### 前端状态

- [ ] 建立 Session store，保存 `session_id`、`project_id` 和最新 `state_version`。
- [ ] 为 Session 创建生成稳定 `request_id`。
- [ ] 为每个用户动作生成并管理稳定 `command_id`。
- [ ] 命令成功后原子替换完整 SessionState。
- [ ] `STALE_STATE` 时刷新并要求确认。
- [ ] 页面刷新后恢复最近 Session。

### UI

- [ ] AgentChat 改为显示后端 SSE 安全事件。
- [ ] 根据 `outstanding_questions` 渲染澄清表单。
- [ ] 根据 `legal_actions` 渲染操作按钮。
- [ ] Spec 视图展示 Markdown、revision、status、reviews 和 findings。
- [ ] 人工审核支持 approve、reject、rework。
- [ ] rework 后支持 revise。
- [ ] approved 后支持 convert_to_work_item。

### 测试

- [ ] Session 创建和重复 request ID 测试。
- [ ] 澄清多轮测试。
- [ ] legal actions UI 测试。
- [ ] stale state 冲突测试。
- [ ] Agent failure/invalid result 测试。
- [ ] approve/reject/rework/revise/convert 状态矩阵测试。

### 验收

- [ ] 用户可从 Project Brief 走到 `AGENT_SPECS_READY`。
- [ ] UI 不允许执行后端未开放的动作。
- [ ] 页面刷新不会丢失 Session 身份和当前阶段。

## 5. Phase 3：真实 WorkItem 与只读 DAG

### 映射层

- [ ] 实现 `WorkItemRead -> WorkItemViewModel`。
- [ ] 映射 ROOT/MILESTONE/TASK。
- [ ] 通过 dependency IDs 派生 DAG edge。
- [ ] 将角色头像、颜色等限定为本地装饰数据。
- [ ] 缺失工时、进度、提交和运行状态时显示“未提供”。

### UI

- [ ] API 模式移除看板的拖拽写状态行为。
- [ ] API 模式移除“自动执行”“解除阻塞”“部署”等操作。
- [ ] DAG 改为只读规划依赖图。
- [ ] WorkItem 详情展示 objective、scope、outputs、acceptance criteria、skills 和 dependencies。
- [ ] 关联展示 Agent Spec。

### 测试

- [ ] 空 WorkItem 列表。
- [ ] 三层 ROOT/MILESTONE/TASK。
- [ ] 多依赖 DAG。
- [ ] 可选字段为空。
- [ ] 不得显示 mock commit/log 的回归测试。

### 验收

- [ ] 看板和 DAG 全部来自真实后端数据。
- [ ] UI 不暗示 WorkItem 已被真实执行。

## 6. Phase 4：PRD 行批注与异步发布

### 后端

- [ ] 新增 `GET /prd/{wi}/commentable-lines` DTO。
- [ ] 从 Gitea changed-file patch 计算 context/addition 行。
- [ ] 返回 version、filename、commit SHA 和允许行。
- [ ] 保留创建评论时的最终 diff 校验。
- [ ] 增加 commit 漂移和不可批注行测试。

### 前端 API

- [ ] 实现 latest/version/versions。
- [ ] 实现 comments 查询。
- [ ] 实现 commentable lines 查询。
- [ ] 实现 create/reply/resolve。
- [ ] 实现 publish 和 task polling。
- [ ] 保存当前 task ID，以支持刷新恢复。

### PRD UI

- [ ] 删除浏览器生成 Markdown、版本号和 commit hash 的逻辑。
- [ ] Markdown 按后端行号稳定渲染。
- [ ] 只在 commentable lines 显示批注入口。
- [ ] 支持多条本地暂存，但逐条提交。
- [ ] 显示每条评论的成功、失败和重试状态。
- [ ] publish 后显示 pending/processing/done/error。
- [ ] done 后刷新所有相关资源。
- [ ] Agent 回复与人工 resolve 分开呈现。
- [ ] 支持人工 reply、resolve 和 unresolve。

### 测试

- [ ] 有效行/删除行/越界行测试。
- [ ] 多评论部分失败测试。
- [ ] publish 无未解决评论测试。
- [ ] 202 任务轮询测试。
- [ ] 页面刷新恢复 task 测试。
- [ ] task error 和恢复测试。
- [ ] Agent 回复不自动 resolve 测试。

### 验收

- [ ] PRD 演进全程没有前端伪造数据。
- [ ] Gitea comment、reply、resolved 和 commit 与页面一致。
- [ ] 任务完成后新 Spec 仍遵守后端审核门禁。

## 7. Phase 5：历史版本恢复为 n+1

### 后端

- [ ] 在 `CommandAction` 增加 `restore_spec_version`。
- [ ] 在状态机中明确合法状态。
- [ ] 校验 source revision 属于同一项目。
- [ ] 以当前 Spec 为 parent 创建 n+1。
- [ ] 记录 `generation_source=HISTORICAL_RESTORE`。
- [ ] 记录 source revision、reason、actor 和 command 证据。
- [ ] 复用 RULE + Reviewer Agent 审核。
- [ ] 保持 command 幂等、CAS 和失败恢复语义。
- [ ] 确认新 PRD 文件通过既有 binding 流程生成。

### 前端

- [ ] 历史版本菜单将“回滚”改名为“基于此版本创建新版”。
- [ ] 显示不可覆盖历史的说明。
- [ ] 要求填写恢复原因。
- [ ] 使用最新 `state_version` 提交命令。
- [ ] 完成后刷新 Session、Spec 和 PRD 列表。

### 测试

- [ ] 从 v1 恢复并创建 v4。
- [ ] source revision 不存在。
- [ ] source revision 属于其他项目。
- [ ] stale state。
- [ ] 重复 command ID 幂等。
- [ ] RULE/Reviewer 失败。
- [ ] 历史 Spec 和 Gitea 文件未被修改。

### 验收

- [ ] 恢复操作始终创建 n+1。
- [ ] 新版本重新进入审核，不自动批准。
- [ ] parent/source 审计链可查询。

## 8. Phase 6：安全审计轨迹

### 后端

- [ ] 定义安全 `TraceEvent` schema。
- [ ] 为 AgentCall 绑定 `trace_id`。
- [ ] 保存 phase、tool call checkpoint、hash、status、timing 和稳定错误码。
- [ ] 模型提供 reasoning summary 时保存安全摘要。
- [ ] 明确公开事件和受限诊断数据的字段边界。
- [ ] 防止 token、完整敏感 prompt、stderr 和原始私有推理进入 `/events`。
- [ ] 增加脱敏与越权读取测试。

### 前端

- [ ] AgentChat 显示安全阶段事件。
- [ ] WorkItem/Spec 审计区显示事件时间线。
- [ ] 明确区分“阶段摘要”与“模型回复”。
- [ ] 不提供“显示完整 thought chain”入口。

### 验收

- [ ] 每次 Agent 调用都能关联到命令、Session、Spec/Task 和最终结果。
- [ ] 公开 API 无敏感诊断信息。
- [ ] 失败可以通过 trace/checkpoint 定位到稳定阶段。

## 9. Phase 7：清理、验证与交付

### 清理

- [ ] API 模式移除随机 ID、随机 commit 和 setTimeout 执行模拟。
- [ ] Code Editor、Git Commits 和部署入口在 API 模式隐藏或标记未启用。
- [ ] 删除未使用的 Gemini/AI Studio 环境说明，或明确其仅属于 mock 模式。
- [ ] 更新前端架构文档，使其不再声明未实现接口。
- [ ] 更新后端工程说明，记录新增 identity/commentable-lines/restore/trace 契约。

### 验证

- [ ] 后端单元测试。
- [ ] 后端集成测试。
- [ ] 前端 TypeScript 检查。
- [ ] 前端组件与 API client 测试。
- [ ] 生产构建。
- [ ] fake Agent + fake Gitea 的端到端测试。
- [ ] 浏览器手工 smoke test。
- [ ] 故障注入：Agent 失败、Gitea 超时、409、重启中断任务。

### 交付门禁

- [ ] 所有 P0 验收标准通过。
- [ ] 无浏览器端密钥。
- [ ] 无真实模式静默 mock。
- [ ] 无私有推理泄露。
- [ ] 单 worker 部署约束被文档和启动配置强制说明。

## 10. 后续阶段候选项（不属于本次）

- WorkItem 执行状态机和子 Agent 调度。
- 跨进程任务队列。
- 代码仓库浏览、commit/diff API。
- 测试、构建和部署执行器。
- WebSocket 多人协同。
- 企业 JWT/SSO 与细粒度 RBAC。
- Google Drive 备份权限收敛和服务端托管。

