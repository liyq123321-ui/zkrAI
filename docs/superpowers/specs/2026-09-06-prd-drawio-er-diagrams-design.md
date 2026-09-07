# PRD draw.io ER 图生成、展示与回写设计

## 背景

当前 Project Spec 只包含结构化文字字段，`_render_markdown` 将这些字段稳定地渲染为 Markdown。Gitea PRD Diff 和前端正文都只读取这份 Markdown；正文使用 `react-markdown`，并明确禁用了图片。结构化 Agent 运行在临时工作目录和仅含认证信息的临时 `CODEX_HOME` 中，不会加载仓库或用户的 skill。

本功能在不破坏 Agent 隔离、PRD 不可变版本和 Gitea 行批注语义的前提下，引入固定版本的 `drawio-skill`，在确有实体关系需要表达时生成 ER 图。PRD 正文将图显示为可拖动、缩放的只读组件；Diff 只显示图名或版本链接。用户可在官方 draw.io 编辑器中修改图，并将保存结果回写为新的 PRD 版本。

## 目标

- PRD 初次生成和根据批注修订时，可按需求证据决定是否生成一个或多个 ER 图。
- ER 图由明确加载的 `drawio-skill` 指导生成，产物保持为可编辑 draw.io XML，而非扁平图片。
- 图插入到 PRD 正文中与其内容最相关的章节之后。
- 正文内支持拖动画布、滚轮或控件缩放、复位和全屏，不支持直接编辑节点或关系。
- 提供“在 draw.io 中编辑”入口；保存后将 XML 回传并创建不可变的 `n+1` PRD 版本。
- Diff 页面不渲染图形、不展开 XML，只显示图名和带内容版本的链接。
- 保留当前 Gitea 批注、权限、乐观并发和人工审核门禁。

## 非目标

- 不在 PRD 正文组件中实现节点、字段或连线编辑。
- 不让任意用户级 skill、`AGENTS.md`、记忆或偏好进入结构化 Agent 运行时。
- 不让 Agent 运行 draw.io 桌面 GUI，也不要求后端安装 draw.io Desktop 或 Graphviz。
- 不从 ER 图反向自动改写功能需求、验收标准或数据库迁移代码。
- 不在 Diff 中提供图形级视觉差异比较。

## 方案选择

采用“结构化 XML + Markdown 版本链接 + 正文交互渲染”方案：

1. draw.io XML 保存在 `SpecVersion.content` 的 ER 图集合中。
2. Markdown 只保存稳定、可审核的图名与版本链接。
3. PRD 读取 API 同时返回 Markdown 和当前版本的图数据。
4. 正文渲染器将专用链接替换为交互式图组件；Diff 保持纯文本。

没有采用把 XML 直接嵌入 Markdown 的方案，因为长 XML 会污染 Diff、放大 Gitea patch，并影响行号批注。也没有采用仅导出 PNG/SVG 的方案，因为扁平图片不能满足后续 draw.io 编辑和 XML 回写需求。

## 固定的 drawio-skill 供应链

仓库内引入 `Agents365-ai/drawio-skill` 的 `skills/drawio-skill` 目录，固定到提交：

```text
65f5fa0505f43d8af104d00c6087cb02c8c0e2f3
```

该提交的 skill 元数据版本为 `3.2.1`、许可证为 MIT。仓库同时保存来源、提交 SHA、许可证和规范化目录 SHA-256，升级必须显式更新这些证据，不跟随上游 `main` 漂移。

结构化 Runner 继续使用临时中立工作目录、`--ignore-user-config` 和只读 sandbox。只有 `pm_generate_spec` 与 `pm_rewrite_prd` 两个节点会把固定的 `drawio-skill` 复制到临时 `CODEX_HOME/skills/drawio-skill`；其他节点仍只有认证文件。节点提示明确要求：只在需求证据足以识别两个以上持久化实体及其关系时生成 ER 图；不得为了装饰而生成；不得访问网络、启动 GUI或写出旁路文件；最终 XML 必须作为结构化 JSON 字段返回。

`--ignore-user-config` 只忽略 `CODEX_HOME/config.toml`，因此不会恢复用户配置；测试还会直接验证临时运行时只包含认证文件和这一项被允许的 skill。若当前 Codex CLI 不能发现该目录，启动前检查将明确失败，而不是静默退化为普通提示生成。

## 领域模型

新增向后兼容的 ER 图结构：

```text
ErDiagram
  diagram_id       稳定、项目内唯一的 slug
  title            正文与 Diff 显示名称
  after_section    插入位置，必须是已知 PRD section key
  drawio_xml       未压缩的 mxfile/mxGraphModel XML
```

`ProjectSpecPayload.er_diagrams` 为列表，默认空列表以兼容历史 Spec；严格 Agent 输出 Schema 仍要求新生成结果显式返回该字段。初次生成可返回零个或多个图。修订时，语义未变化的图必须保留 `diagram_id`；新增、删除或语义变化必须在 `change_summary` 中说明。

允许的 `after_section` 限定为最可能承载数据关系语义的章节：

- `functional_requirements`
- `system_boundaries`
- `core_objects`
- `main_flows`

同一章节有多张图时保持 Agent 返回顺序。`diagram_id` 不作为数据库主键；版本身份由 `SpecVersion` 与内容哈希共同确定。

## XML 验证

Agent 输出和人工回写共用同一个纯函数验证器。验证发生在持久化和渲染之前，包括：

- 最大 XML 字节数、最大页面数、节点数和边数；
- 禁止 `DOCTYPE`、外部实体、脚本、事件处理器、外部图片和可执行链接；
- 根节点只能是支持的 `mxfile` 或 `mxGraphModel`，只接受未压缩 XML；
- cell ID 唯一，边的 source/target 必须指向存在的节点；
- ER 图至少包含两个实体节点和一条有效关系边；
- 允许 skill 生成的 `html=1` 样式标志，但标签内容按纯文本处理，不接受实际 HTML、脚本或链接；
- `diagram_id`、标题和插入章节合法，且同一版本内 ID 唯一。

验证失败时，Agent 生成走现有最多三次结构化输出修复；人工编辑回写返回稳定的 `INVALID_DRAWIO_DIAGRAM`，不创建部分版本。

## Markdown 与 Diff

`_render_markdown` 在指定章节内容之后插入单行链接：

```markdown
[ER 图：订单数据模型](#firstflight-er-order-model-7f3a1c9d)
```

末尾短哈希由该图规范化 XML 计算，因此布局、样式或关系发生变化都会生成新链接。图的 XML 不进入 Markdown。

后端 Diff 继续对相邻版本 Markdown 做统一 diff。新增、删除或修改 ER 图时只会出现链接行变化；Diff API 不返回图 XML，Diff 前端也不挂载 ER 图组件。链接行仍使用真实 Markdown 源行号，因此 Gitea 行批注不会发生行号重映射。

## PRD 读取与正文展示

`PrdDocumentRead` 增加 `er_diagrams`，每项包含 `diagram_id`、`title`、`anchor` 和 `drawio_xml`。后端从当前 `PrdVersion.spec_version_id` 指向的不可变 Spec 读取，验证结构化内容哈希和图哈希后返回；历史版本接口返回对应历史图，不串用当前图。

前端 `react-markdown` 的自定义链接渲染器只拦截 `#firstflight-er-...`。匹配成功且 API 中存在同锚点图时，渲染 `DrawioErDiagram`；普通链接保持现有行为。组件提供：

- 鼠标或触控拖动画布；
- 控件缩放、支持的滚轮/触控板缩放；
- 适应画布、复位和全屏；
- 下载 `.drawio`；
- “在 draw.io 中编辑”。

查看器使用 draw.io core `31.4.2` 的 `viewer-static.min.js`，连同 Apache-2.0 许可证和来源证据随前端打包；日常查看不依赖外网。组件在隔离容器中加载已经过后端验证的 XML，不执行 XML 内的链接或脚本。无法加载时显示图名、错误提示、重试、下载和外部编辑入口，不影响其余 PRD 正文。

## 外部 draw.io 编辑与回写

“在 draw.io 中编辑”使用官方 embed 协议打开独立编辑窗口。前端保留窗口句柄并执行 JSON `postMessage` 握手：收到 `init` 后发送当前 XML；收到 `save` 后先在本地保留草稿，再提交后端。只有明确来源为配置的 diagrams.net origin、结构合法且对应当前编辑会话的消息会被接收。

新增受现有 reviewer 权限保护的接口：

```text
POST /prd/{wi}/diagrams/{diagram_id}/revisions
```

请求包含：

```text
base_version
base_commit_sha
drawio_xml
change_summary
```

后端处理顺序：

1. 复用 PRD write preflight，验证 actor、根 WorkItem、当前审核状态、Gitea binding 和权限。
2. 比较 `base_version`、`base_commit_sha` 与当前绑定；不一致返回 `PRD_CONTENT_CONFLICT`，不覆盖并发更新。
3. 校验 XML；规范化后与当前图相同则返回 no-change，不创建版本。
4. 克隆当前结构化 Spec，只替换目标图，保留 `input_refs` 和其余字段。
5. 创建不可变 `n+1` Spec，并重新渲染带新图哈希链接的 Markdown。
6. 将新 PRD 版本发布到原有长期 Gitea PR，记录结构化内容哈希、Markdown 哈希、父版本和人工编辑来源。
7. 对新版本运行现有规则与语义自动审核；通过后回到 `HUMAN_REVIEW`，发现问题则进入现有返工/澄清状态。保存图绝不会自动批准或进入任务拆分。

接口使用后台任务，前端复用现有 PRD 发布状态提示和轮询体验。成功后重新加载正文、Diff、可批注行、评论和版本数。外部 draw.io 页面没有本系统后端凭据，不能直接写入；所有回写都由原 PRD 页面执行。

弹窗被拦截、用户关闭编辑窗口或网络失败时，前端保留本次 XML 草稿并提供重试提交或下载，不把未确认内容显示为已保存版本。

## 修订生成行为

`pm_rewrite_prd` 会收到当前 `er_diagrams` 以及冻结的 Gitea 评论：

- 评论只修改文字且不影响实体关系时，保留原图及 ID；
- 评论改变实体、主外键或关系时，使用 drawio-skill 同步更新相关图；
- 评论删除产生该图的业务范围时，删除图；
- 证据不足时不得猜测数据库字段或基数，保留文字假设或阻塞问题；
- XML 与结构化文字的实体名称必须一致。

自动 reviewer 检查图名、实体名称和 `core_objects`/需求之间的明显冲突，但不把布局差异当成语义缺陷。

## 权限、审计与版本语义

- 查看图沿用现有 PRD 读取规则和降级行为，不额外放宽写权限。
- 外部编辑回写只允许现有 final approver、project manager 或 root owner。
- 每次有效回写记录 actor、父版本、目标 `diagram_id`、旧/新图哈希和 change summary；审计信息不保存原始窗口消息。
- 任意已发布版本都可按版本号恢复和下载对应 XML。
- 图内容属于 `SpecVersion.content` 的不可变部分；Gitea Markdown 仍是文字与图引用的评审载体，`spec_content_hash` 继续证明绑定的完整结构化版本。

## 错误处理

- Skill 缺失、完整性不匹配或 Codex 无法发现：启动/调用前失败为配置错误，不静默生成无 skill 的 ER 图。
- Agent 返回非法 XML：进入结构化修复；三次失败后沿用 `INVALID_AGENT_RESULT`。
- API 图锚点与 XML 哈希不匹配：拒绝返回交互图并报告内容冲突。
- 查看器初始化失败：正文降级为图名、重试、下载和外部编辑入口。
- draw.io 编辑窗口来源或会话不匹配：忽略消息并提示编辑会话失效。
- 回写发生版本冲突：保留本地草稿，要求刷新后重新比较，不自动覆盖。
- Gitea 发布部分失败：沿用现有发布任务恢复证据，不把数据库版本标记为已完成。

## 测试策略

### 后端单元测试

- 历史 `ProjectSpecPayload` 没有 `er_diagrams` 时仍可读取；新 Agent Schema 要求显式输出该字段。
- XML 验证覆盖合法 ER 图、危险 XML、重复 ID、悬空边、超限内容和无关系图。
- Markdown 在正确章节后生成单行、带哈希链接；Markdown 中不含 XML。
- 图 XML 变化会改变链接哈希，普通文字变化不会改变未修改图的链接。
- Runner 只为 PRD 生成/修订复制固定 skill，并保持无用户配置、规则或其他 skill。

### 后端集成测试

- 初次生成带图、无图和多章节图。
- Gitea PRD Diff 只含图名/链接，不含 `mxGraphModel`、`mxCell` 或 XML。
- PRD 当前与历史版本分别返回正确图数据和哈希。
- 人工回写创建 `n+1`、保留旧版本、经过自动审核后回到人工审核或返工/澄清状态，并记录审计。
- no-change 不创建版本；越权、非法 XML、过期 base version 和发布恢复均无部分副作用。
- 基于评论的文字修订保留未受影响图；实体关系修订更新图链接。

### 前端测试

- 正文专用链接替换为 ER 图组件，普通链接不受影响。
- 拖动、缩放、适应画布、复位、全屏和下载可用。
- Diff 只显示名称/链接，永不挂载 ER 图组件。
- embed 编辑握手校验 origin、会话和消息类型；保存后提交正确 base version/commit。
- 弹窗拦截、关闭、查看器失败、网络失败和版本冲突均保留可恢复草稿。
- 保存成功后刷新到新版本，未保存内容不冒充当前版本。

### 验证

- 运行相关后端单元、集成和 API 测试，再运行完整后端测试。
- 运行前端组件/API 测试、类型检查和生产构建，再运行完整前端测试。
- 使用包含 0、1、多张 ER 图以及一次外部编辑回写的示例 PRD 做浏览器人工验证，确认正文交互、Diff 文本和版本历史。

## 发布与兼容

- 数据库字段不新增列；ER 图随已有 JSON `SpecVersion.content` 保存，历史行通过默认空列表兼容。
- 公共 DTO 只做向后兼容的新增字段；前端缺少图字段时按空列表处理。
- drawio-skill 和查看器均固定版本，不在运行时下载。
- README、后端运行文档和变更日志说明 skill 来源、更新方式、查看/编辑行为及“保存会创建新 PRD 版本”。
