# 本机部署记录

部署日期：2026-09-03。上游版本：`64e6c71`。

本页以下各节为分阶段记录。此前澄清与拆分修复已提交并推送 `main`（`38b2085`），真实项目已保存 3 个里程碑、12 个子任务及对应 Spec。最新的“子任务详细实现规格”已加载本机前后端，代码尚未提交，详细记录见 [修改日志](../CHANGELOG.md)。

## 2026-09-03 子任务详细实现规格

- 任务详情新增对应需求原文、具体实施步骤、接口与字段定义、数据结构、异常行为和设计决策。建议接口和技术选择明确标注为提案。
- 新生成任务要求具备实现方案；已有任务可通过 `backend/scripts/enrich_agent_specs.py` 基于批准 PRD 补齐，整体审核通过后统一保存，并校验运行期间项目数据未变化。
- 后端 539 项、前端 44 项测试通过；前端类型检查、构建及代码复核通过。已重新加载 LaunchAgent 后端并构建前端，真实页面可正常读取历史 Spec。
- 用户已授权的系统内 Agent 生成与复审已完成，会话 `767932e2-b720-4065-98e3-099119f29880` 的 12 份 Spec 已统一写回，共 93 个实施步骤、37 个接口定义、91 个数据结构定义。
- 最终独立审核调用 `5cf83d50-7cd2-4f66-86ba-867f28b3148e` 为 `SUCCEEDED`，结论 `PASS`、发现为空。项目仍为 `AGENT_SPECS_READY`，状态版本由 19 更新为 20。
- 只读比对确认批准 PRD v3 整行、16 个工作项、36 条依赖和 12 个 Spec 标识均未变化；原 Spec 内容仅补充需求原文与实施方案，内容哈希一致。已刷新真实页面并打开任务详情核对展示。

## 访问

- 前端：http://127.0.0.1:3001/
- 后端健康检查：http://127.0.0.1:8088/healthz
- API 文档：http://127.0.0.1:8088/docs
- Gitea PRD 仓库：http://localhost:3000/tang/docs

所有服务仅绑定本机回环地址。前端使用真实 API 模式。

## 运行方式

前端由 Docker 容器 `zkrai-web` 中的 Nginx 托管，挂载 `frontend/aios-main/dist`，设有 `unless-stopped` 重启策略。需要 Docker Desktop 运行。

后端由 macOS LaunchAgent `local.zkrai.backend` 运行，登录后自动启动，固定为单进程、单 worker。配置文件：`/Users/tangtang/Library/LaunchAgents/local.zkrai.backend.plist`。

后端使用独立的 Python 3.12 虚拟环境 `backend/.venv`。Codex 调用复用本机现有登录，按仓库示例使用 `gpt-5.6-luna`，并启用 `--ignore-user-config`。

## 配置与数据

- 后端配置：`backend/.env`，权限为 `0600`，含 Gitea 令牌，已被 Git 忽略。
- 前端配置：`frontend/aios-main/.env`，已被 Git 忽略。
- 应用数据库：`backend/data/gateway.db`。
- 后端日志：`/Users/tangtang/Library/Logs/zkrAI/`。
- Gitea 数据：Docker 卷 `gitea-data`，文档仓库为 `tang/docs`，基础分支为 `main`。

不要将真实令牌、`.env` 或运行数据提交到 Git。

## 常用操作

重启：

```sh
docker restart zkrai-web
launchctl kickstart -k gui/$(id -u)/local.zkrai.backend
```

停止：

```sh
docker stop zkrai-web
launchctl bootout gui/$(id -u) /Users/tangtang/Library/LaunchAgents/local.zkrai.backend.plist
```

再次启动：

```sh
docker start zkrai-web
launchctl bootstrap gui/$(id -u) /Users/tangtang/Library/LaunchAgents/local.zkrai.backend.plist
```

前端代码更新后重新构建：

```sh
cd /Users/tangtang/Desktop/zkrAI/frontend/aios-main
npm ci --no-audit --no-fund
npm run build
docker restart zkrai-web
```

后端依赖更新后安装并重启：

```sh
cd /Users/tangtang/Desktop/zkrAI/backend
.venv/bin/python -m pip install -r requirements.txt
launchctl kickstart -k gui/$(id -u)/local.zkrai.backend
```

## 本次验证

- 前端生产构建、TypeScript 检查、20 项测试通过。
- 后端 389 项测试通过。
- 后端健康检查返回 `status=ok`、`database=ok`。
- 项目自身的 Gitea 能力预检通过。
- 浏览器成功创建真实测试会话，PM Agent 返回澄清问题，事件已持久化。
- 测试会话 ID：`637cd85f-c5eb-447e-bff1-e2f3342e7a6f`，内容已标注为部署测试数据。

完整 PRD 生成及评审流程未在生产数据上执行；相关逻辑已通过仓库现有自动测试。

## 2026-09-03 PRD 工作台修复

已更新本机前后端服务。当前代码分支：`codex/fix-prd-review-workspace`，尚未提交。

- 修复 Gitea 1.27.3 PR files 不包含 patch 导致 PRD 审核无法打开的问题，改为读取官方 PR diff 并准确提取目标文件。
- PRD 审核以对话框打开，默认显示真实 Diff，可切换完整 Markdown 正文或并排阅读。Diff 对比仓库基础分支，首次新增文件全部显示为新增。
- 正文独立加载，批注、评论或 Diff 失败时仍可阅读，并提供重试；数据不完整、正文与 PR HEAD 不一致或读取期间 base/head 变化时禁止审核操作。
- 审计记录可展开执行状态、事件数据、相关 PRD 和审核发现。
- Session 区改为“项目进度”，显示中文状态、PRD 版本和下一步；状态版本属于流程更新次数，区别于 PRD 版本；无历史版本时隐藏无效恢复操作。
- 本机真实项目只读验证：正文、可批注行、Diff 均返回 200，版本 v1、提交一致，可批注 154 行。浏览器确认完整正文与 Diff 并排显示、单栏切换正常，审计审核详情可展开。
- 回归验证共 409 项测试通过，独立审查复查通过。未替用户批准、修改或重新生成现有 PRD。

后续界面调整：按用户要求将默认阅读模式改为 Diff；按行批注输入框移至文档区域下方、“审核发现”上方。正文和并排查看仍可切换。

## 2026-09-03 跳过审核澄清并拆分子任务

“提交澄清”下新增“跳过澄清，确认当前 PRD 并拆分子任务”。明确确认当前版本后，后端原子保存跳过记录、人工审核和审计，前端直接执行拆解。初始需求阶段的跳过仍生成第一版 PRD。拆解失败时，可通过独立的“继续拆分子任务”入口重试，复用原命令标识，不依赖 Gitea 文档加载。

后端 406 项、前端 31 项测试通过，类型检查、生产构建及代码复核通过。已加载本机服务，并通过浏览器核对新按钮及后端健康状态。未代替用户点击真实项目的确认按钮；代码尚未提交 Git。

## 2026-09-03 PRD 与拆分结构缺漏自动修复

PRD 生成、审核意见改写和子任务拆分现在共用有上限的输出修复循环：JSON/schema 与结构一致性错误最多共三次尝试。检查并修复未定义需求引用、验收覆盖及验证方法、资料引用、任务依赖与产物；改写时也检查冻结评论回复是否完整且无重复或未知编号。最终仍通过规则与语义审核，命令原子落库，已确认 PRD 保留原文。

427 项后端测试通过；真实失败拆分结果经只读回放验证，未修改该项目数据。最终后端已通过 LaunchAgent 重载，健康检查正常。代码尚未提交 Git。
