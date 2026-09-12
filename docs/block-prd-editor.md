# Block PRD 编辑器

本次是在现有 React + FastAPI + SQLite 项目中直接增加功能；按用户明确指示，不对这次代码修改另行套用 SDLC 项目规划。验收采用一次整体验收。

## 使用

1. 从项目对话区点击「编写 PRD」，或从 PRD 审核页点击「编写 PRD · Block 编辑」。独立页面地址为 `/prd-editor/<session_id>`，支持刷新恢复和返回项目。选定项目生命周期路线后的入口也直接打开编辑器，不再先等待整篇正文生成。
2. 新文档立即显示阿里模板目录。点击「AI 规划大纲」只生成结构；「导入大纲」可切换鹅厂模板、粘贴 Markdown/纯文本目录，或上传文件。PDF/TXT/Markdown 文件先展示提取文字供确认，再导入并规划。PDF 限 8 MB、50 页，扫描件或加密文件明确报错。
3. 选择一个 Block，生成、编辑或添加批注。文字输入约 900 毫秒后保存；也可手动保存。切换 Block 保留各自未保存输入。保存失败会显示原因，不丢弃草稿。
4. 选中文字或填写起止行可定位批注；勾选左侧目录可对多个 Block 添加同一条批注。「按此批注修改各块」为每个目标创建独立作业。旧版本行批注不会静默套用到新文本。
5. AI 结果先保存为候选。页面只在无未保存输入、基线仍匹配时自动应用。否则显示 Diff、基于当前版本重新生成、仍然应用。明确应用也检查当前版本，不能无条件覆盖。刷新时可重新看到未处理候选。
6. 「历史版本」可比较并恢复旧正文；恢复创建新版本，不回退计数。修改上游会传递标记下游已有正文为「待更新」，不自动重写。依赖在当前块的「上游依赖」中维护；规划图显示层级和当前块的上游关系。
7. 「审核当前块」仅提取该块的结构化规格并返回意见，不改正文。人工修改后提取结果失效。提交评审前，每个有正文的块和叶子块须完成内容与结构同步；没有正文的目录容器不需要生成占位文本。
8. 「提交评审」确定性汇总 Block 正文和结构片段，校验完整性、需求 ID、验收引用及其他既有规格规则，创建 `HUMAN_REVIEW` 状态的 `SpecVersion`。不自动批准、拆分或发布。错误提示会指出待补内容；不会偷偷回退到旧规格或调用 AI 重写整篇。

## 现有数据与运行方式

- 启动时在已有数据库中新增 `prd_documents`、`prd_blocks`、`prd_block_versions`、`prd_block_comments`、`prd_agent_runs`，不重建已有表、不改旧 PRD。
- 每个 Session 一份持续编辑的草稿。首次打开已有 PRD 时按标题层级导入正文；标准规格章节同时保留对应结构字段和图引用。已存在正文、历史编辑或批注时，替换大纲会被阻止，以保护内容。
- 旧版本和 Gitea 审核记录继续保留；新 Block 快照通过既有 PRD 读取/绑定入口进入评审。旧 Gitea 评论与新 Block 批注分别保存。Block 快照的编辑按钮回到 Block 编辑器。
- `CodexProvider` 复用现有 `CodexStructuredRunner`，只向它提供当前块、当前反馈、文档背景/规则及必要的上游摘要，不提供整篇文档和聊天历史。使用现有 Codex CLI 配置和登录状态。可通过 `create_app(block_provider=...)` 注入其他实现。
- 单后端进程运行，最多 3 个 AI 调用并发，同块只能有一个活动作业。不要为该任务协调器配置多个 Uvicorn worker。进程重启后未结束作业标记 `PROCESS_INTERRUPTED`，可重试；关闭/取消会回收 CLI 进程。
- SSE 推送持久化快照，前端保留 5 秒轮询兜底；作业不依赖页面保持打开。批注、历史和候选均存入 SQLite。
- 所有新接口沿用 `ActorResolver`，校验项目审批人/经理/负责人权限。部署身份模式沿用现有配置；浏览器不自行伪造身份。

后端新增依赖 `pypdf>=5,<7`，已列入 `backend/requirements.txt`。更新现有服务时安装后端依赖、重启后端，并刷新前端；静态前端需重新构建。本次只运行独立测试库的本机预览，没有替换现有运行服务或操作生产数据。

2026-09-12：按用户要求更新并重启本机服务。现有 `zkrai-web` 容器将 `frontend/aios-main/dist` 挂载为静态目录；重新构建后重启容器即可加载新版本。`frontend/aios-main/nginx.conf` 提供 React 路由回退，容器内配置位置为 `/etc/nginx/conf.d/default.conf`，重建容器时也应挂载或复制此配置，否则直接访问 `/prd-editor/<session_id>` 会返回 404。后端继续使用 `backend/.env` 和原有 SQLite 数据库，在 8088 端口运行。

## 代码位置

- `backend/app/models/`：五类新增持久化模型。
- `backend/app/ai/`：Provider 接口、结构化返回合同与 Codex 适配。
- `backend/app/services/block_service.py`：事务保存、历史、依赖、批注和评审快照。
- `backend/app/services/ai_service.py`：作业调度、版本保护、取消与恢复。
- `backend/app/api/documents.py`、`blocks.py`：文档、Block 和 SSE 接口。
- `frontend/aios-main/src/api/prd-editor/`：全屏编辑器和独立组件。

## 验证记录（2026-09-11）

- 新增后端测试覆盖模板解析、结构循环/缺失引用、并发 SQLite 保存、历史恢复、幂等作业、权限、行与多块批注、摘要白名单、依赖/全局背景变化、取消、错误 AI 返回、重启恢复、旧 PRD 导入及与下游一致的规格快照。
- 新增前端 7 项测试覆盖进入页面不调用整篇生成、切换块保留输入、保存中继续输入、未保存草稿与 AI 结果竞争、正常结果应用和逐行 Diff。
- 浏览器独立测试库验证了生成期间手工保存、旧版本冲突、Diff、明确应用到新版本及历史记录。
- 真实 Codex CLI 成功生成一个两句话的独立 Block，返回正确 `block_id`、`base_version`、正文、摘要和本块结构片段。
- 最终针对性验证：后端 93 项、前端 21 项测试通过（其中新增 Block 后端 22 项、前端 7 项）。
- TypeScript 检查与生产构建通过。构建保留现有单包体积提示。
- 旧工作区测试有 35 项失败，旧后端 Session 测试有 9 项失败；均在 Git `1a5245a` 的独立副本、相同配置下复现。没有修改这些旧测试或降低校验。新功能相关测试及数据库、Codex 调用、PRD API 回归测试通过。

复验命令：

```bash
backend/.venv/bin/python -m pytest backend/tests/integration/test_block_prd.py backend/tests/unit/test_database_schema.py backend/tests/unit/test_agent_gateway.py backend/tests/integration/test_prd_review_api.py -q
npm --prefix frontend/aios-main run lint
npm --prefix frontend/aios-main test -- --run src/api/prd-editor/PRDEditor.test.tsx src/api/PrdDocumentViews.test.tsx src/api/WorkspaceDashboard.test.tsx src/api/client.test.ts
npm --prefix frontend/aios-main run build
```
