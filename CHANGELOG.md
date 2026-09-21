# 修改日志

## 2026-09-21 — 修复 Semantica 决策溯源「无法连接 Neo4j 图谱数据库」

- **根因不是服务没开**：Neo4j 容器 `memory-neo4j` 一直 `Up`（宿主 7474/7687，重启策略 `unless-stopped`），
  真正的原因是 `backend/.env` 里 `SEMATICA_NEO4J_PASSWORD=` 为空。Neo4j **不把空密码当免密**，而是直接回
  `AuthError ... Unsupported authentication token, missing key 'credentials'`；
  于是 `/semantica/status` 呈现 `configured=true, available=false`，前端面板显示连不上——现象与「服务未启动」极像。
- 修复：`backend/.env` 与 `backend/.env.example` 都补上本机容器凭据 `SEMATICA_NEO4J_PASSWORD=neo4j123`
  （来自 `docker inspect memory-neo4j` 的 `NEO4J_AUTH=neo4j/neo4j123`），并加注释说明留空会被拒。
  `SEMATICA_ENABLED=true` 原本已是显式开启，无需改动。
- 重启后端加载新配置后 `/semantica/status` → `{"configured":true,"available":true,...}`。
- 端到端验证（真实 Neo4j，非 mock）：用 `.env` 构造 `Settings` 走 `SemanticaRecorder` 写入一条 runtime event +
  一条 LLM decision，再用审计面板同款查询 `query_decisions(project_id, agent_session_id)` 读回成功；
  `GET /semantica/decisions?project_id=...` 返回 `{"available":true,"decisions":[...]}`；
  图谱落库标签为 `Project / AgentSession / AgentCall / SoftwareAgent / AgentEvent / Decision`。探针节点已清理。
- 空库语义：数据不会回填历史，只有**新的** agent 活动才会写入；面板此时展示「该 Agent 会话暂无 Semantica 决策记录。」
  而不是「无法连接」。
- 文档：`docs/SEMANTICA_AUDIT.md` 新增「排查：面板显示无法连接 Neo4j」小节（状态接口三态判读、空密码陷阱、
  bolt 连通性自证、改 `.env` 必须重启、本机默认拓扑），并更正 `SEMATICA_NEO4J_PASSWORD` 的默认值说明。
  同时如实记录两处事实：`SEMATICA_GRAPH_NAME` 当前**未被** `_build_graph()` 使用（隔离只靠节点属性，
  因此与其它 stack 共用 `memory-neo4j` 存在标签串味风险）；semantica 0.6.8 使用 `id()`，Neo4j 5.26 会报
  `01N02 DEPRECATION` 警告（Neo4j 6 将失效）。
- 运维：新增项目技能 `.workbuddy/skills/firstflight-local-runtime/`，固化本机三件套（8088 后端 / 3001 前端 /
  Neo4j 图谱）的拉起与排障流程，含两个已实际踩到的坑——沙箱会回收 `Popen(DETACHED_PROCESS)` 的子进程
  （须用受管后台任务），以及基础 Python 无 uvicorn（须用 `.venv/Scripts/python.exe`）。

## 2026-09-21 — PRD 页「关闭」改为一步返回项目顶层图，并去掉按钮文字

- 嵌入式 PRD 编辑器左上角的返回控件改为**图标按钮**：只保留 `ArrowLeft` 箭头，文字「关闭 PRD」删除，可访问名与 `title` 仍保留「关闭 PRD 编辑器」。
- 关闭不再弹确认框：无未保存草稿时**直接返回**，不再出现「关闭 PRD 编辑窗口？」。同样的短路逻辑接到命令式关闭路径（`requestClose`），使切换 Tab / 切换任务 / 新建任务时的离开也一步完成。
- 唯一的例外是**存在未保存草稿**（`unsavedCount > 0`）：此时仍弹出一次保存提示，避免静默丢弃已输入正文。这是本次唯一保留的确认步骤。
- 返回目标由用户主页（`dashboard`）改为**该项目的顶层图视图**（`topLevel`）：`ApiWorkspace` 中 `EmbeddedPRDEditor` 的 `onClose` 改为 `setActiveTab('topLevel')`。提示文案同步改为「关闭后将返回该项目的顶层图视图。」
- 验证：`PRDEditor.test.tsx` 新增/改写 2 项（无草稿时箭头按钮一步关闭且 `textContent` 为空 + 命令式路径同样一步关闭；有草稿时仍拦下并可在保存后离开），`workspace.test.tsx` 新增 1 项（关闭 PRD 后顶层图面板为唯一 active 的 `ff-tab-pane`、PRD 编辑器卸载），均通过；`tsc --noEmit` 通过。
- 已知既存失败（与本次改动无关，已用源码核对确认）：`PRDEditor.test.tsx` 3 项断言的是已被删掉的旧文案（`全文 DOCX 编辑`、`正在写入全文 · 2 句`、`首版正在按模块连续生成（1/2）`，这三个字符串如今只存在于测试文件里）；`workspace.test.tsx` 34 项为既有失败，改动前后数量一致。

## 2026-09-21 — 顶层图新增「查看 PRD / 查看原型」入口，并把 PRD 定型接到原型生成

- 顶层图工具栏新增两个按钮：**查看 PRD**（等价于原「编写 PRD」，直接切到 PRD 编辑器）与 **查看原型**（在新标签页打开当前项目的 HTML 原型，原页面不动）。
- 「查看原型」完全复用既有原型契约，不新增读接口：`GET /prd/{wi}` 已返回绑定原型（`prototype.status` / `content_url`），HTML 由 `GET /prd/{wi}/v/{version}/prototype`（带 sandbox CSP）提供。`wi` 取当前项目的 ROOT 工单。
- 三态提示：已生成 → 新标签页打开 `apiBaseUrl + content_url`；未生成 → 提示「HTML 原型还在生成中，请稍候再试。」；失败 → 呈现 `error_message`。PRD 未定型（`current_spec_version_id` 为空）时提示先完成「进入下一阶段」，不发请求。
- 把「进入下一阶段」（PRD 定型）接到既有生成流程：`BlockService.submit` 回传 `project_id`；`/prd-documents/{id}/submit` 用 `BackgroundTasks` 调 `PrdPrototypeService.automatic_generation_allowed()` + `ensure_for_project()`，与 Spec 命令路径、评审发布路径共用同一服务，生成仍在 PRD 提交落库之后进行，原型失败不影响 PRD 提交结果。
- 提示条样式 `.ff-top-level-notice`（info / warn / error 三态）新增于 `frontend/aios-main/src/index.css`。
- 验证：新增 `tests/integration/test_prd_submit_prototype.py`（3 项：回传 project_id 并触发生成 / 有未解决审核项时跳过 / 未注入原型服务时仍成功），并在 `test_block_prd.py` 锁定 `submit` 的 `project_id` 契约；`tests/unit/test_prd_prototype.py` + `test_prd_review_api.py` + `test_prd_review_service.py` 共 56 项通过。前端新增 5 项 `workspace.test.tsx` 用例（PRD 入口、原型就绪开新标签、未就绪提示、失败提示、未定型提示），`tsc --noEmit` 通过。
- 实测（真实库）：温度换算器项目 `GET /prd/{wi}` 返回 `prototype.status=ready`，其 `content_url` 可取回 10,242 字节 HTML；未定型的 Todo List 项目返回 404，前端走「尚未定型」提示。

## 2026-09-21 — 登录权限角色分类（RBAC）

- 新增角色分类，作为登录/权限层的单一事实来源 `backend/app/auth/roles.py`：租户管理员（`tenant_admin`）、项目经理（`project_manager`）、一般员工（`employee`）、专家（`expert`）、只读（`viewer`），每个角色携带**定位 / 默认操作点 / 说明**及元数据（默认唯一、默认成员角色、可否兼任业务角色）。
- 定义 23 个操作点（code + 中文名 + 分组），角色默认操作点与之绑定；`view.*` 为前缀通配（只读）。
- 分配现状：`owner-1` → 项目经理，`admin` → 租户管理员，其余角色暂不分配。
- 后端接入：`require_admin` 改为按租户管理员判定（保留别名，旧代码不动）；新增 `require_operation(op)` 依赖工厂；新增 `GET /api/auth/roles` 下发分类表；`/api/auth/login` 与 `/api/auth/me` 返回 `role` / `role_name` / `operations`。
- 用户管理 API：`role` 校验改为五个 code（`VALID_ROLES`），默认角色为一般员工；租户管理员默认唯一（创建/启用第二个返回 `400 TENANT_ADMIN_EXISTS`，最后一个不可删除）；列表返回 `role_name`。
- 新增 `RBAC_ALLOW_TENANT_ADMIN_BUSINESS`（默认 `false`）：开启后租户管理员叠加项目经理的业务操作点，即"可配置放开"；默认租户管理员只做管理、不做业务。
- 兼容与迁移：`normalize_role()` 把历史值 `owner → project_manager`、`admin → tenant_admin` 等映射到规范 code，未知值回退一般员工；启动迁移 `_migrate_sqlite_user_roles()` 就地归一化 `users.role`（幂等），种子逻辑同时修正内置账号。查询/鉴权路径用严格版 `get_role()`（未知值返回 `None`），与宽松归一化分离。
- 前端：`session.ts` 增加 `RoleCode` / `ROLE_LABELS` / `roleLabel()` / `isTenantAdmin()`（`isAdmin` 保留别名）/ `hasOperation()`；`SettingsPanel` 显示角色中文名且「用户管理」仅对租户管理员可见；`AdminUsersPage` 角色下拉改为五个角色并新增**角色分类表**（角色/定位/默认操作点/说明，取自 `/api/auth/roles`，带本地兜底）。
- 新增文档 `docs/RBAC_ROLES.md`：分类表、操作点清单、接口契约、关键规则、配置项与迁移说明。
- 验证：新增 `tests/unit/test_roles.py`（45 项）与 `tests/integration/test_rbac_roles.py`（21 项），前端 `src/auth/session.test.ts`（8 项），全部通过；`tsc --noEmit` 通过。

## 2026-09-21 — Agent 行为审计层（Semantica + Neo4j）

- 引入 `semantica[graph-neo4j]==0.6.8` 作为 Agent 行为审计底座，把项目运行时的 Agent 行为镜像为 Neo4j 属性图，用于审计与未来可扩展性（MCP + REST Explorer）。
- 新增 `backend/app/audit/recorder.py`：通过 `GraphStore`（整数内部 id）落库 `Project / AgentSession / AgentCall / AgentEvent / Decision / SoftwareAgent` 六类节点，以及 `HAS_SESSION / HAS_CALL / EMITTED / HAS_DECISION / WAS_GENERATED_BY / WAS_ATTRIBUTED_TO / WAS_ASSOCIATED_WITH` 七类关系；内置熔断器（失败 300 秒冷却）与空上下文跳过，未安装/不可达时安全降级为 no-op。
- 在事件总闸 `backend/app/services/agent_runtime_events.py:append()` 镜像运行时事件，在 LLM 总闸 `backend/app/ai/codex_provider.py:_call()` 镜像关键决策，两处均为 best-effort 不阻断主流程。
- 新增启动器 `backend/tools/run_semantica_server.py`：`.env` 驱动的 REST Explorer 启动入口，支持 `--check` 校验与 `--print-mcp-config` 输出 MCP stdio 客户端配置（MCP 在 0.6.8 仅 stdio 传输）。
- 新增 `backend/tests/test_audit_recorder.py`：用内存假 GraphStore 验证禁用 no-op、事件+决策落库、空上下文跳过、熔断器触发，全部 4 项用例通过。
- `backend/app/config.py` 增加全部 `SEMATICA_*` 配置项；REST Explorer 端口按上游硬编码统一为 `8000`。
- 新增审计文档 `docs/SEMANTICA_AUDIT.md`：节点/关系词汇表、接入步骤、WebGUI 审计 Tab 集成说明、Cypher 审计查询示例与 MCP / REST Explorer 接入方式。
- **默认开启**：`SEMATICA_ENABLED` 默认值改为 `true`（`config.py` 与 `.env` / `.env.example` 同步），Neo4j 不可达时自动熔断降级，不影响主流程。
- 审计 tab（`AgentRuntimePanel`）接入 Semantica：标题状态徽标反映审计层健康度；打开 agent 运行记录弹窗可查看该会话在 Neo4j PROV 图中捕获的“决策溯源”（推理、结果契约、置信度）。后端新增只读接口 `GET /semantica/status` 与 `GET /semantica/decisions?project_id=&agent_session_id=`，均未启用/不可达时优雅降级。
- 验证：10 项后端测试通过（含 `query_decisions` 项目/会话过滤、`strict` 异常透传、`status.available` 真实连通性与探测缓存）；前端 `tsc --noEmit` 通过、`AgentRuntimePanel` 12 项测试通过；`.env` 默认 `SEMATICA_ENABLED=true`，未配置 Neo4j 时 recorder 自动旁路。
- 修复：`status.available` 原先只看开关，Neo4j 未起时也会误报“已连接”；现改为对图谱做轻量探测（30s 缓存），并在 `query_decisions(strict=True)` 时透传连接错误，使 `/semantica/decisions` 能区分 `disabled` / `unreachable`。同时收敛审计 tab 决策查询的 effect 依赖，避免 2 秒轮询导致的重复拉取与加载闪烁。

## 2026-09-06 — PRD draw.io ER 图生成、正文交互与版本回写

- PRD 初次生成和批注修订节点引入固定且完整性校验的 `drawio-skill`；仅在证据明确存在至少两个持久化实体及关系时生成 ER 图，Reviewer 检查图与正文语义一致性。
- draw.io XML 随不可变 Spec 保存，并经过实体/关系结构、体积、页面、链接、脚本、HTML 和外部资源安全校验；历史 Spec 默认无图。
- PRD 正文使用本地固定的 draw.io 31.4.2 查看器，支持拖动、缩放、适应、复位、全屏和 `.drawio` 下载；Diff 只显示图名/哈希链接，不传输或渲染 XML。
- “在 draw.io 中编辑”通过严格 origin/window 绑定的官方 embed 协议工作；保存先保留本地草稿，再以 base version/commit 乐观并发回写为 `n+1`，重新自动审核并继续人工门禁。
- 相同 XML 不建版本；非法、越权、旧版冲突、网络失败和发布中断均保留明确结果与可恢复证据，不自动批准、拆分或覆盖当前 PRD。
- 供应链来源分别固定在 `backend/skills/drawio-skill-source.json` 和 `frontend/aios-main/public/drawio/31.4.2/SOURCE.json`。

## 2026-09-03 — 依赖 WorkItem 详情跳转

- 任务详情的依赖关系改为显示名称与编号的可点击入口，在当前对话框打开对应 WorkItem，并将滚动和键盘焦点返回顶部。
- 从完整任务集合查找依赖，跳转不受看板筛选影响，保留原任务草稿和筛选条件；不可用依赖明确提示，尚无 PRD 的 Root 显示基本信息。
- 本次修改前端及说明文档。23 项相关回归测试、类型检查、生产构建通过；实际页面已验证从「编写需求文档与运行说明」跳转至「固化需求范围与验收口径」。

## 2026-09-03 — 数据库共享看板与主任务多选

- 看板通过新增只读 `GET /sessions` 接口读取数据库全部项目，不依赖浏览器历史。新建任务与左侧项目对话切换保留右侧卡片及筛选；新增任务并入共享集合。
- 搜索框旁增加「主任务」下拉多选，选项使用 Root 主 WorkItem 名称；选择后同时展示该项目的里程碑和全部子 WorkItem，支持全部任务、清空选择，与搜索/类型/执行者组合筛选。
- 项目数据独立保存于页面，详情、PRD 审核命令、状态版本和草稿始终绑定所属 Session。项目加载失败不会清空其他项目，可通过刷新看板重试。
- 同步更新需求、用户指南、架构说明和 OpenAPI。新增接口只读，不改变任务状态或触发 Agent。
- 验证：前端 54 项测试、类型检查与构建通过；后端 23 项 Session 接口测试通过；独立代码复核未发现阻断问题。
- 实际页面已读取 2 个 Root、3 个里程碑、12 个子任务；核对取消选择时对应子项一起隐藏、新建表单保留筛选及卡片。

## 2026-09-03 — 项目对话增加新任务入口

- 左侧 Agent 对话面板顶部增加「创建新任务」按钮，点击打开现有新项目需求表单，保留原项目数据；提交「创建并分析」后才启动创建流程。
- 请求处理中禁用按钮，操作栏支持换行。同步更新用户指南和需求文件，仅修改前端与文档。
- 验证：17 项工作区回归测试、类型检查、生产构建通过；已在实际页面核对按钮布局与表单切换。

## 2026-09-03 — 子任务对话入口与员工选择（前端）

- 子 WorkItem 详情顶部增加子 Agent 对话区域、人工指令优先说明、指令输入框和修改需求快捷入口。保留发送及中断按钮，未接入真实 Agent 前保持禁用。
- 责任 Agent 改为员工下拉选择，兼容当前项目负责人、示例员工和未指派状态；选择同步到本页看板、任务流转图和执行者筛选。
- 指令草稿和指派预览按 Session、WorkItem 隔离保存在页面内存，关闭详情后重开保留，刷新后恢复；不新增网络请求，不保存至服务端。
- 员工目录独立管理并由上层传入详情组件，便于后续接入数据库名单。示例员工明确标记，页面说明功能当前范围。
- 更新用户指南、前端架构说明及需求文件。本次只修改前端与文档，未更改后端、数据库、PRD 或真实任务状态。
- 验证：前端 50 项测试、类型检查和生产构建通过；实际任务的草稿、指派预览、关闭重开与窄屏布局已核对。

## 2026-09-03 — 子任务详细实现规格

- 每份新任务 Spec 增加实现概述和顺序实施步骤；每一步关联正式需求，说明具体方法、交付物和验证方式。
- 增加接口定义、输入输出字段及类型/必填约束、异常处理、数据结构及校验规则，记录已确认约束与建议设计的区别。
- 对应需求原文由服务从批准 PRD 复制；步骤遗漏需求、引用未知或未分配需求、重复步骤/接口字段/数据结构、空白方法等不能进入最终保存。
- 初始拆分、局部修订及 Reviewer 共用详细规格要求；为已有任务提供单独的实现方案生成和审核流程，保留原任务身份与范围。
- 整体审核检查跨任务接口、枚举、来源标识、必填字段和数值单位的一致性，避免分开生成的方案无法衔接。
- 任务详情改为可读的需求、步骤、接口和数据定义分块；历史 Spec 继续可读，只从绑定来源 PRD 补充需求显示，缺失方案时明确提示。
- 增加本机补齐命令 `backend/scripts/enrich_agent_specs.py`，执行者必须是项目已授权的负责人或审核人。
- 补齐流程最多并行生成两份方案，完成后整体审核，最多两轮针对性修订；再次核对批准 PRD、任务及操作者快照后统一保存，失败不写入半套方案。
- 已生成方案在内存中及时提供给后续排队任务，使其复用已有生产者接口或显式定义适配，不必等整轮结束才看到其他方案；正式保存仍要求整体审核通过。
- 增加完整实施方案校正稿的独立复审入口，绑定原拒绝审核与当前快照，记录准确候选哈希，仅采用新审核结论；不把此前不同内容的 PM 结果伪记为已采用。
- 验证：后端 539 项、前端 44 项测试通过；前端类型检查、生产构建和代码复核通过。新前后端已加载，本机现有任务详情兼容读取正常。
- 用户授权的系统内 Agent 生成、修订和独立复审已完成：当前项目 12 份历史 Spec 全部保存详细方案，共 93 个实施步骤、37 个接口定义、91 个数据结构定义；最终审核 PASS、无发现。
- 统一了入口/上下文/回答/引用/安全模块的调用交接、共享超时预算、条件认证、分类澄清、可空失败字段及验收材料与实际执行记录的职责。相关要求同步进入后续生成规则。
- 本次只更新任务需求原文副本与实施方案，批准 PRD v3、任务身份和依赖保持不变，项目状态版本由 19 更新为 20。已刷新真实页面，核对实施步骤、接口字段及数据定义显示。

## 2026-09-03 — 澄清回写、PRD 审核与子任务自动修订

本次汇总基于 `64e6c71` 的全部项目改动，目标分支为 `main`。

### 澄清回复同步到 Project Brief

- 修复澄清回复已保存、正式 `project.brief` 未更新，导致后续反复要求“Brief 同步”的问题。命令入口与直接服务入口均保存结构化 `brief_updates`，并记录更新字段。
- 根据回复时间整理完整澄清历史，避免 PRD 换版后轮次重置使旧决定覆盖新决定。已确认的范围、交付物、排除项、资料和验收描述可供后续生成与审核读取。
- 未提供的字段保留原值；明确提交的列表替换旧列表，允许清空已废弃内容。审批人和平台权限不由模型自动修改。

### PRD 阅读、Diff 与项目进度

- 修复 Gitea PR files 响应缺少 `patch` 时审核页面无法打开的问题，回退读取 PR diff 并提取目标文件；增加 `/prd/{wi}/diff` 接口及 OpenAPI 契约。
- 验证 Diff 对应的正文与 PR base/head，拒绝读取期间版本变化或正文冲突引发的审核写入。
- 用对话框查看 PRD，支持 Diff、Markdown 正文和并排阅读，默认显示 Diff；批注输入框位于文档下方。
- 正文可独立加载，评论或 Diff 暂不可用时保留正文并允许重试；审核数据不完整时禁用确认与发布。
- 项目区域显示中文阶段、当前 PRD 和下一步操作；审计记录可展开状态、相关文档和审核发现；子任务卡片可查看对应 Agent Spec。

### 跳过审核澄清并继续拆分

- 在“提交澄清”下增加“跳过澄清，确认当前 PRD 并拆分子任务”按钮。
- 已有 PRD 时，校验明确确认、当前版本、操作者及状态版本，原子记录人工确认和审计，然后拆分当前 PRD；初始澄清阶段仍按原流程生成 PRD。
- 拆分失败保留已确认状态，提供“继续拆分子任务”入口，并复用原命令的幂等标识。

### 自动补齐结构缺漏与语义修订

- PRD 生成、批注改写和任务拆分共用结构化输出修复：检查未知需求编号、验收覆盖、验证方法、引用、依赖、产物及改写评论回复覆盖。JSON/schema 与结构错误合计最多三次尝试。
- 拆分语义审核拒绝后，将原方案和具体反馈交回 PM，严格依据已确认 PRD 自动修订并独立复审，最多两轮语义修订。
- 复用同一 PRD 的历史拒绝结果；同命令恢复保持已消耗的修订轮次，避免重试重置预算。
- 语义修订只返回受影响的已有条目。服务按键合并后校验、复审完整方案，保留未修改任务；禁止未知键、重复替换、删除或重命名。
- 审核同时参考任务范围、排除项、约束和扩展点，区分未来实施交付与当前规划证据；需要新人工决定的问题继续保留。
- 每轮保存调用及审核证据。最终再次核对批准 PRD 的版本、正文、哈希和输入引用，再原子保存任务与 Agent Spec；失败不保存半套任务。

### 实际运行验证

- 会话：`767932e2-b720-4065-98e3-099119f29880`。
- 全量修订曾触发 `Codex timed out after 900 seconds`。加载局部修订版本后，重试约 144 秒完成两轮修订与复审，最终 `PASS`，无审核发现。
- 已保存 3 个里程碑、12 个子任务和 12 个 Agent Spec，状态为 `AGENT_SPECS_READY`、状态版本 19；已确认 PRD v3 的正文、哈希和批准状态一致。
- 浏览器已显示“任务规格已就绪”和正确卡片数量，旧错误提示已清除。

### 提交前验证与配套文档

- 后端：`backend/.venv/bin/python -m pytest backend/tests -q --tb=short`，441 项通过。
- 前端：`npm test`，4 个测试文件、31 项通过；`npm run lint` 与 `npm run build` 通过。
- 存在两个既有 Python 依赖弃用提示，以及 Vite 的 MockApp 大包提示，不影响本次测试或构建结果。
- 新增本机部署记录与四份实现/验证记录，更新 README、依赖锁文件和接口文档；根目录忽略 macOS `.DS_Store`。
- `.env`、凭据、运行数据库及生成的构建文件继续按已有规则保留在本机，不纳入版本库。

详细记录见 [本机部署](docs/local-deployment.md) 和 [自动语义修订](docs/superpowers/plans/2026-09-03-semantic-breakdown-repair.md)。
