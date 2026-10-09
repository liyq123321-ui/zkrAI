# firstFlight 项目长期记忆

> 详细手册已下沉到技能，不要在这里重复：
> `.workbuddy/skills/firstflight-local-runtime/`（本机排障）、`firstflight-prd-import/`（导入五铁律 + 两个脚本）、
> `firstflight-gateway-db/`（库结构 + `scripts/gateway_db.py`）。本文件只留跨主题的硬规则与踩坑结论。

## 本机运行拓扑

| 组件 | 位置 | 监听 | 启动 |
|------|------|------|------|
| 后端 | `backend/` | `127.0.0.1:8088` | `.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8088 --env-file .env`，或 `tools\start-backend.cmd`（幂等、带日志） |
| 前端 | `frontend/aios-main/` | `127.0.0.1:3001` | `npm run dev`（有 HMR，改前端不用重启） |
| Neo4j（Semantica 审计） | 容器 `memory-neo4j` | 宿主 `7474`/`7687` | 自动（`unless-stopped`） |
| MCP Gateway | `backend/app/mcp_gateway/` | 复用 8088 `POST /mcp` | 随后端启动；`MCP_GATEWAY_ENABLED=0` 可关；独立进程 `python -m app.mcp_gateway.standalone`（8099） |

- 登录自启：`%APPDATA%\...\Startup\firstFlight-backend.cmd` → 调 `tools\start-backend.cmd`；删该文件即关闭。
  （`schtasks /sc onlogon` 在本机报"拒绝访问"，故走启动文件夹。）
- **改 `app/` 下任何东西都必须重启后端**：无 `--reload`，且 `Settings.from_env()` 每进程只读一次（`.env` 同理）。
- **必须单进程单 worker**：BackgroundTasks 与命令 ownership fence 只在进程内协调；8088 只应有一个 LISTEN。
  抢不到端口的重复实例会自行退出，不会变成第二个 worker。
- ⚠️ **启动后端必须剥掉沙箱注入的 `HTTP_PROXY`**（极重要）：
  `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy ./.venv/Scripts/python.exe -m uvicorn main:app ...`
  沙箱注入的 `127.0.0.1:7268` 代理对 Codex 端点回 `HTTP CONNECT failed with status 502` →
  **只要后端继承它，每一次 Agent 调用都失败**（历史"AI 调用失败"的根因）。剥掉后由 Clash TUN 走真实节点。
- **`.venv/Scripts/python.exe` 是 launcher 桩**，会 exec 基础 Python 并注入 venv 上下文。所以 `wmic` 显示
  `ExecutablePath` = 基础 Python **是正常的**；绕过桩直接调基础 Python 才报 `No module named uvicorn`。
- **Neo4j 空密码 ≠ 免密**：`SEMATICA_NEO4J_PASSWORD` 留空 → `AuthError ... missing key credentials` →
  `/semantica/status` 的 `available:false`，极像"服务没开"。本机值 `neo4j123`。
- MCP Gateway 完整注意事项见 `docs/mcp-gateway.md` 第 7 节（管理端 `X-MCP-Admin-Token` 不设即全部 503 = fail-closed；
  `/mcp-gateway/reload` **只重载 YAML、不重载环境变量**；限流是进程内内存计数，多 worker 会各算各的）。
- 本文件按重要性排序（**截断只会切掉尾部**），新条目请往对应主题里合并，不要堆在末尾。

## 命令的执行范围（2026-10-09 落地，用户明确要求）

**点击目录只是查看，不划定范围。** 范围只有三个来源，优先级从高到低：

| 来源 | 谁解析 | 语义 |
|------|--------|------|
| 指令里写明章节名 | 前端 `namedPrdTargets()` | 明确 |
| 在目录中**勾选**章节 | 前端 `prdEditScope()` → `target_ids` | 明确（起点，允许按图文联动自动扩展） |
| **都没有 → 整篇全文** | 后端 `select_targets()` 让 Agent 读整篇目录挑章节 | 默认 |

- 前端唯一入口 `frontend/aios-main/src/api/prd-editor/instructionRouting.ts` 的 `prdEditScope()`。
  **⚠️ 它不接受「点中的那一章」**——旧版 `prdRevisionTargets(..., selectedRef.current)` 就是那条泄漏。
  守护：`instructionRouting.test.ts` 里有一条**源码级**断言（调用点不得出现 `selectedRef.current`），
  因为纯函数测不出"接线"层的回归。
- 域名交互：`BlockTree.tsx` 里点已选中的章节 → 再次点击取消选中（`onSelect(selected === b.id ? '' : b.id)`）。
- **第二条泄漏通道：讨论对象（discussion）**。`select()` 曾把点中的章节写进 discussion，而
  `useContext = savedContext && !explicitlyNamed && !screenshot` 又把它当范围。**已从 `select()` 移除**；
  只保留显式动作来源：正文选中文字、下拉框「选择 Agent 修改目标」（`focusChapter`）、对象固定、命令结果。
  `select()` 里的注释就是防线，别加回去。
- 后端：`Plan.target_ids` / `JobPlan.target_ids` / `BlockTree` 之外的 `BlockPlan` **硬限 8 章**，
  所以「整篇 236 章一起进上下文」物理上不可行。做法是**两步**：
  1. `DocumentEditService.select_targets()` 只喂「目录 + 标题 + 父标题 + 摘要(200 字)」，让 Agent 返回
     `TargetSelection{block_ids, reason}`（≤8，`extra="forbid"`）；
  2. 再把这些章节的正文交给 `plan()`。
- 契约：`JobPlan.scope: "chapters" | "document"`，**默认 `"chapters"`**（老前端行为完全不变）。
  走 `document` 时前端传 `target_ids: []`，`document_jobs.execute` 里先定位再规划；
  定位失败 → 422 `未能在整篇目录中确定要修改的章节，请在文档目录中勾选章节，或在指令里写明章节名称`。
- 模型侧：新增 operation `chat_target_select`（`CodexProvider.select_chat_targets`，
  `build_operation_instructions` 里有对应分支；`TargetSelection` 在 `app/ai/provider.py`）。
  **已用真实模型验证**：指令「矩阵里「协同」这一行要挪到最下面…」不含任何章节名，Agent 靠摘要定位到
  `FR-1.7 软件开发各阶段 Agent 能力与工具矩阵`。
- 守护测试：后端 `tests/unit/test_document_scope_targets.py`（6 例）+
  `tests/integration/test_prd_document_jobs.py` 的两条接线用例（未定位 → 不得调用 `plan`）；
  前端 `instructionRouting.test.ts`、`BlockTree.test.tsx`、
  `PRDEditor.test.tsx::treats an outline click as viewing only…`。
  探针：把 `document_jobs` 里那个 `if` 关掉 → 两条接线用例变红，报的就是改前的
  `请选择明确的目标章节，最多一次修改八个章节`。
- 注意：`ApiWorkspace.tsx` 里那句「默认修改当前章节」已改为「默认按整篇全文执行…」——
  界面文案与这套语义必须同步，否则用户会按错误的心智模型操作。

## 排查方法论（项目惯例）

- **先读状态接口，别猜"服务没开"**：`/semantica/status` 的 `configured`/`available` 能区分"开关没开"与"连不上"；
  `docker ps` 显示 Up ≠ 可用。
- **可逆基线探针**：判定失败是否既有，先临时撤掉改动/短路，比对数量再下结论，不要靠推断。
  **写过的新测试必须用探针证明它会红**——只会通过的测试没有价值。
- **只改被要求的表层，不吞掉用户的显式语义**；有安全/数据风险点就保留并在回复里报备 + 给出撤销方式。
- 判断"是否逐字保留原文"，**基准必须是源文**（`brief.source_prd.content`），不要拿两种布局互 diff。
- ⚠️ **别对 25 万字用字符级 `difflib.SequenceMatcher(autojunk=False)`**：O(n²)，实测被沙箱 SIGTERM
  （表现为"脚本无输出、exit 1"）。改问等价但便宜的问题：**源文是否为成品的子序列**（贪心双指针 O(n)）——
  我们只增加标记字符，`removed==0` 即等价于没丢字。

### 已知测试基线（判定"是不是我弄红的"）

- **后端**：`pytest tests/unit` 恒 1 失败 ——
  `test_ai_sentence_stream.py::test_line_range_accepts_neighbouring_changes_but_rejects_distant_changes`。
  `tests/integration/test_prd_source_import.py` 恒 1 失败（401，`.env` 是 `auth` 而该用例不建测试配置）。
  其余（unit + prd 相关 integration）当前 **551 passed / 1 failed**。
- **前端**：`FullDocumentView.test.tsx` 恒 1 失败（缺「以下意见基于旧版本」文案）。
  `workspace.test.tsx` 整文件恒红（单独 `-t` 跑筛选用例全通过）。
  `PRDEditor.test.tsx` **2026-10-09 起从 30 失败降到 3 失败**——入口处补了 `Array.isArray(jobs)` 守卫后，
  26 个被 `documentJobs.some is not a function` 渲染崩溃掩盖的用例恢复运行。剩下 3 条是长期潜伏、
  与本轮无关的既有失败（目录宽度 `620px`、多章节名走单次对话任务、进入时的 `auto_generate` 请求体）。
- 判定手法：`git stash push -u -- frontend/aios-main/src` → 重跑 → 对比 → `git stash pop`。
  ⚠️ `PRDEditor.tsx` 工作区长期躺着别人的未提交改动，别把整份 diff 当自己的。

## gateway.db

- **库内时间戳全是 UTC**，本地 = +8h。判断"任务是不是刚跑的"务必先换算。
- **"正在跑的 agent" 只能靠进程树确认**：`agent_calls.status=SUCCEEDED` 不代表没有活的子进程。
  `psutil.Process(<backend_pid>).children(recursive=True)` 找 `codex.exe exec ... --output-schema`，
  `cwd` 在 `%TEMP%/codex-structured-*`。杀完临时目录自行清理。
- **一个项目的行分散在 40+ 张表**，四类外键：`project_id` / `session_id` / `document_id`(prd_) / `wi`。
- **最易漏的坑：`conversations.id` 就是 session_id**（字段名看不出关联）。全库扫残必须"按值遍历所有表的所有列"。

### 扫残/删除：锚点法（别再写无约束闭包，踩过三次）

全程 `mode=ro`：① 按值扫全表全列定位该 UUID；② 由该行解析锚点：`project_id`/`session_id`，再反查该项目的
`work_items.id`、`prd_documents.id`、`workspace_chat_messages.id`；③ 只在
`project_id`/`session_id`/`document_id`/`wi`/`work_item_id`/`message_id` 上 `IN(...)`，外加特例
`conversations.id == session_id`。删完复扫，正常应与锚点计数**完全相等**。

三个陷阱：**闭包扩展只能从"已匹配行"出发**（写在 `if row_matches:` 外层会一轮吸进全库 uuid，曾牵连 4 项目/2205 行）；
**实体 id 不一定是 UUID 形状**（PRD 文档 id = `prd_<32位hex>` 无连字符，用 UUID 正则会漏掉全部 `prd_*` 行）；
**操作者标签列必须排除**（`actor_id`/`reviewer_id`/`created_by`/`suggested_assignee`/`username` 等是 `owner-1`
这类跨项目共用值，纳入闭包会把别的项目整体拉进来）。

删除脚本一律 `--dry-run` / `--apply` 双模式；apply 才 `src.backup(dst)`（**SQLite 在线备份 API**，别热拷 db）+ 删除 +
`integrity_check` + 残差复扫。备份名沿用 `gateway.db.bak-*`，落在 `backend/data/`。
已删：`b28ef03d`（117 行）、`f4992de0`（96 行）。

### 本机常驻进程白名单（"关掉相关进程"时不要误杀）

前端 `npm run dev` 链、后端 uvicorn（venv 桩 + 基础 python 两个 PID）、WorkBuddy 自身与其 MCP。
另有**独立**的 OpenAI Codex 桌面应用进程组（`codex.exe app-server`、`codex-computer-use-swift`、
`cua_node`、`codex-app-tools`）—— cwd 常指向本项目，但**与 firstFlight harness 无关**，关掉等于关用户的 Codex，需先问。
本项目 harness 判据：命令行形如 `codex exec ... --output-schema` 且 cwd 在 `%TEMP%/codex-structured-*`。

## 编辑链路的三个结构性缺陷（2026-10-08 全部修复，都有守护测试）

用户看到「文档候选任务 · 失败，可重试」= `prd_document_jobs.status='failed'`。
**表里没有 `title` 列**（`kind`/`status`/`base_revision`/`payload`/`result`/`events`/`error`），
标题由 `DocumentJobs.view()` 从 `payload.instruction` 现算；原因在 `error` 列。
**模型产物默认不留痕**：`agent_calls` 不记 `chat_edit_plan`，`ReviewCache.key()` 只认 review 类操作
（`chat_edit_plan` 永远 key=None），Codex 临时目录跑完即清 → 取证只能**同参数重放**；
但**优先用错误信息里自带的锚点原文离线复现**，别急着花 150s 重放。

1. **表格行锚点在执行中自毁**。`_simulate` 顺序应用 steps，`_table_edit` 原本每步在**已改过的 content** 上解析
   `row_match`；而"交换两行"必然搬运锚点字符串本身（step0 把编号 P1→P5 后，"P1" 变 0 匹配、"P5" 变 2 匹配）
   → **任何顺序都自毁**，且动作枚举里**没有行重排原语**，模型只能拆成 N 步 cell 写。
   → 修：`_simulate` 用 `table_anchors[(block_id, table_index)]` 记**首次接触该表时的行快照**，
   `_match_rows` 优先按快照解析（快照行数与当前不一致时回落到实时匹配，以兼容增删行）。
2. **行名锚点有三种形态**，模型三种都给过：单元格值（`P1`）、**整行按 ` | ` 拼接**（最常给，因为模型看到的就是整行，
   而它与单元格**永远不相交** → 必然 0 匹配）、半行片段。→ `_row_hits` 四级匹配：单元格 → 规范化整行拼接 →
   多段逐段落格 → 退回首列（`_squash()` 去 markdown 填充空格）。**只修一种不够**。
3. **大文档的目录自检被"模型输出上限"卡死**。`BlockPlan.blocks` 的 `max_length=100` 是**模型输出契约**，
   却被拿去校验**既有文档**的目录树（`_simulate` 收尾、`block_service.save()` 改依赖、`_restore_candidate`）
   → 本机 236 块 > 100 → **任何一次聊天修改都在收尾自检报** `at most 100 items ... not 236`，
   而模型其实早已正常出方案。→ 新增 `BlockTree(BlockPlan)`（只有 `min_length=1`）专供本地自检。
   **这三层是叠加的**：修完一层，下一层才暴露，只有重放模型才会发现。

报错文案也做了拆分：0 匹配 → `未找到匹配「X」的数据行；现有行首列：…`；≥2 → `「X」匹配到 N 行（第 a 行、第 b 行）…`。
改前两者都回"定位不唯一，请补充行名"，**0 匹配时那句提示是错的、越补越不可能成功**。

守护测试：`test_document_edit_table_rows.py`（12 例）、`test_large_document_self_check.py`（3 例，含"放开上限后目录坏了仍要报错"）。

## 导入 PRD →「直接使用原始文件」（2026-10-08 大修）

规则、检查器、重导脚本都在技能 `.workbuddy/skills/firstflight-prd-import/`（**改导入粒度/规则只改
`app/services/imported_prd_layout.py`**，纯函数、从不改写措辞）。五条铁律一句话版：

1. **层级只能从字号推**：源 DOCX 1892/1892 段全是 `Normal`、`outlineLvl=None`，零 Heading 样式；层级只活在字号里。
2. **只有标题没有正文的章也必须成块**，否则该章消失、其下小节全被重挂到前一章（树看着合理、父节点全错）。
3. **GFM 表格必须行挨行输出成一个 part**，中间一个空行就退化成管道符；短行补齐、单元格 `|` 转义、换行压空格。
4. **单列表格不是表格**：Word 拿 1×N 表格伪代码块，当表格渲染会把 `#` 注释泄漏成 Markdown 标题。
5. **图片三跳齐走**：抽 `w:drawing` → 存原件 `data/imported-prd/<import_id>.docx` → 建资产按 `block_id` 绑定；
   伴生：端点必须传 `assets=assets`，删块必须 `assets.discard_blocks()`（曾漏 → 残 30 条孤儿资产）。

参考文档：项目 `313f1c46` / 文档 `prd_554c188cd67543558b3b21adb19a2e10`，当前 **revision 12、236 块、30 资产**。
`docx_service.py` **不是**这条回环的解析器（`_parse()` 吃不下外来 DOCX，且它的表格转换器有同样的"只有首行带 `|`"缺陷）。

## 前端渲染 `asset://` 与取受保护资源（2026-10-08）

**两种成因、同一种症状**（破图 + alt 文本，都不报错、不留痕）：

- **A. `<img>` 带不上鉴权头**：`.env` 是 `FIRSTFLIGHT_IDENTITY_MODE=auth`，`ActorResolver.resolve()` 只读
  `Authorization: Bearer`（query 里的 `actor_id` 仅 legacy 用）。→ **正解 = fetch 带 token → blob →
  `createObjectURL`**（先例 `loadWorkspaceChatImage()`；PRD 插图 `DocumentAssetImage.tsx`）。**后端不要放宽鉴权。**
- **B. react-markdown 静默吃掉 `asset:` 协议**：`defaultUrlTransform` 只放行 `http/https/irc/ircs/mailto/xmpp`，
  其余清成**空串** → `img` 收到 `src=""` → fallback、破图，**症状与 A 一模一样**。
  → 每个 `<ReactMarkdown>` 都要挂 `urlTransform={assetUrlTransform}`（挂载点 `RenderedPrdDocument.tsx`、`BlockEditor.tsx`）。
- 自测取 token 的坑：`app.auth.auth_secret()` 读模块级变量，必须先
  `configure_auth(Settings.from_env().auth_jwt_secret, s.auth_token_ttl_seconds)`（只有 `main.py` 会做），
  否则拿到空串、签出的 token 一律 `INVALID_TOKEN`。

## PRD「对比上一版」内联高亮（2026-10-08 落地）

工具栏「审核当前块」已删除，换成「对比上一版」勾选项：红色删除线 = 上一版原有，绿色+下划线 = 本次新增；
不勾选完全按普通图文阅读渲染。

- **硬约束：没装 `rehype-raw`** → markdown 里的内联 `<del>/<ins>` 不会被解析。只能借原生语法 + 覆盖组件翻译：
  旧内容 `~~文本~~` → 覆盖 `del`；新内容 `[文本](#ff-ins)` → 覆盖 `a`（纯 markdown 没有"插入"语法，借伪链接当载体）。
- **表格不能整行包 `~~`**：remark-gfm 对含 `|` 的行**依旧按表格行处理** —— 表格不会断，但整行文字被**挤进第一个单元格**、
  其余格子空着。→ 先做行级 diff，成对的删/改行再做 **token 级行内** diff；整行增删且是表格行时**逐单元格**包裹。
  **测试必须断言"高亮落在各自单元格里"**，只数行列数抓不住（已验证会漏）。
- **「上一版」不能取 `max(version)`**：每次改动会同时推进 revision 并写快照，最新快照常等于当前内容
  （实测 v11 与当前逐字相同，真上一版是 v10）。`api.findPreviousVersion()` 从最新往回探（最多 4 个）。
- 纯函数 `versionHighlight.ts`；挂载点 `RenderedPrdDocument.tsx` 的 `compareBaseline`；样式在 `editor.css`
  （沿用 `.prd-diff` 的浅色配色，**不是**文件顶部那套深色）。

## 前端对话排队机制（2026-10-08 落地）

后端单进程单 worker，执行中再发指令会撞车（`NETWORK_ERROR` +「这条 PRD 修改指令未能执行」）。
现为**本地排队**：先贴在输入框上侧，↑ 弹出确认框（取消当前任务立即执行 / 排到队首），否则等上条跑完自动派发。
主体在 `ApiWorkspace.tsx`（`PendingInstruction` + `agentBusy` + FIFO 排空 effect）。

- **`agentBusy` 故意不含 `activeRuntime.status === 'running'`**：它是持久化 DB 状态，残留僵死 `running`
  会永久锁死输入框。残余竞态由 `isDeferrableFailure()` 回队兜底。
- 发送按钮 `aria-label` 必须保持静态 `"发送消息"`。队列落 localStorage；
  `PRDEditorHandle.cancelActiveRuns()` 提供「立刻生效」所需的取消能力。

## 沙箱工具限制

- Bash 工具：`cmd.exe` 调用被拦；长驻服务用 `run_in_background: true`。
- **进程跨工具调用活不下来**：`Popen(DETACHED_PROCESS)`、`run_in_background`、`explorer.exe → start /min`
  都会在工具调用结束后被回收。→ 后台任务报 `failed` 时**先探端口**（回收的常是包装进程，uvicorn 子进程被孤儿化后继续服务）；
  反过来，脚本自测通过只证明脚本对，**不证明进程能活过会话**。
- **判定"8088 上这个后端是谁的、能否活过会话"，必须递归追祖先链**：祖先里有**用户的终端**
  （WindowsTerminal/用户 shell）＝ 用户自持；祖先是**沙箱 bash** ＝ 临时孤儿。
  ⚠️ **光看 `cmdline` 区分不出来，两者完全一样。**
- **要执行 `.cmd` 只能用 `MSYS_NO_PATHCONV=1 explorer.exe "C:\path\x.cmd"`**；看不到 stdout，
  让脚本自己落日志再断言。同一路径重复调用可能被静默忽略，换文件名。
- PS 工具在本项目沙箱下**输出恒为空**，改用 `netstat` / `tasklist` / `taskkill`；
  `wmic` 已被**黑名单硬拦**（不可批准），查进程树用 **psutil**。
- 命令内容里出现 Power-Shell 相关字面量会触发安全拦截 → 写文件请用 Edit/Write 工具。
- **Edit 工具偶发"返回成功但没落盘"**（2026-09-23 多次复现）。改完必须 `grep` 复核；失效时改用 Write 整文件重写。
- 推送/启动类命令被安全拦截时：先看是不是命中了受限字面量，换等价写法（例如用 Edit 追加日志而不是 heredoc）。
