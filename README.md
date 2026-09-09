# firstFlight 用户指南

firstFlight 第一阶段是一个本地运行的需求澄清、PRD 人工审核和任务拆解工作台。用户提交项目需求后，系统会通过 Agent 判断是否需要澄清；需求明确后生成 PRD，并把 PRD 绑定到主 WorkItem 和 Gitea 行批注流程。PRD 获得人工确认后，后端生成只读的子 WorkItem 与对应 Agent Spec。

当前阶段不会执行子 WorkItem、修改代码或部署应用。页面显示的 WorkItem 与 Agent Spec 是规划产物。

## SDLC 生命周期规划规则

需求澄清、PRD 获批后，自动任务拆分现在加载 `backend/rules/sdlc/` 中的 YAML 规则，
选择纯线性瀑布、阶段重叠瀑布或迭代增量模型。后端强制校验阶段、任务映射、
交付物与门禁依赖；信息不足或路线冲突时阻止落库。规则、使用方法、示例与原文解释见
[SDLC 规划总纲](docs/sdlc/README.md)。总页面的“顶层图”Tab 会按当前任务的选定路线，
纵向显示阶段、准入准出、交付物和执行状态。升级后需重启后端，既有规划记录不自动改写。

## 当前完整流程

1. 在 WebGUI 填写项目目标、范围、约束和验收标准，创建 Session。
2. 如果后端返回待澄清问题，可以填写答案并继续多轮澄清。
3. 如果不希望继续澄清，可以：
   - 点击“跳过澄清并生成 PRD”；或
   - 在澄清框输入明确指令，例如“跳过澄清”“不用澄清”“直接生成 PRD”。
4. 跳过后，Agent 使用保守且合理的假设补齐模糊地带，并在 PRD 中列出重要假设供人工修改。
   如果已有 PRD 且审核再次要求澄清，可以点击“提交澄清”下方的“跳过澄清，确认当前 PRD 并拆分子任务”。该按钮会记录人工确认，直接按当前 PRD 拆分子任务；不会生成新版 PRD。若拆解失败，已确认的 PRD 会保留，可以继续重试拆解。
5. 点击主 WorkItem 卡片打开 PRD；正文会在有数据关系证据时显示可拖动、缩放的 ER 图，Diff 只显示图名链接；也可在有效 Gitea diff 行添加批注、回复或解决批注。
6. 有未解决批注时，确认操作会发布本轮审核并异步生成新版 PRD；没有批注时，确认操作会批准当前 PRD。
7. PRD 批准后，系统自动拆解子 WorkItem 与 Agent Spec。点击子卡片即可查看该角色收到的任务规格。

涉及工作流的页面动作均以后端返回的 `legal_actions` 为准。网络中断或并发更新时，页面会刷新 Session 状态，不会在浏览器中猜测工作流状态。

在左侧 Agent 对话面板顶部点击「创建新任务」，即可进入新项目需求表单，填写后点击「创建并分析」开始新任务。左侧通过「当前对话」切换项目；右侧共享看板始终管理数据库中的所有项目，不会因新建或切换对话而清空。

搜索框旁的「主任务」下拉框支持多选，选项是「项目需求（Root）」中的主 WorkItem 名称。默认显示全部；选择一个或多个主任务后，同时显示它们的里程碑及全部子 WorkItem。可使用「全部任务」恢复全部显示，或「清空选择」隐藏全部。主任务筛选与搜索、类型、执行者筛选共同生效；新建任务不会重置这些条件。

打开页面及点击「刷新看板」时，前端从数据库读取完整项目列表，并加载各项目任务。列表不依赖浏览器访问历史；某个项目暂时加载失败时，其他项目继续显示并提供重试提示。当前对话的选择保存在浏览器，筛选条件和指令草稿只保留在当前页面。

## 子任务对话入口与员工选择（前端预览）

打开子 WorkItem 详情，可在「子 Agent 对话」区域填写指令草稿，或点击「修改需求」开始填写。该入口预留给人工调整需求与执行方向；当前子 Agent 尚未接入，「发送指令」和「中断任务」暂不可用，不会产生回复、修改 PRD 或执行任务。

详情右侧的「依赖关系」显示任务名称和编号，点击可直接打开对应 WorkItem，并回到详情顶部。跳转使用完整任务集合，即使目标被看板筛选隐藏也可打开；原任务草稿和看板筛选保留。暂未加载到的依赖会标注「任务暂不可用」。

「责任 Agent」中的「指派员工」下拉框包含当前项目已有负责人及明确标注的示例员工，也可选择「未指派」。选择会同步显示在本页的看板、任务流转图和执行者筛选中，不会保存到后端。

草稿和员工选择按 Session、WorkItem 分开暂存于页面内存。关闭详情再打开仍会保留；刷新页面后草稿清空、负责人恢复后端原值。员工名单集中在 `frontend/aios-main/src/api/employeeDirectory.ts`，后续可替换为员工数据库接口返回的数据。

详细范围与验收见[子 WorkItem 对话入口与员工选择需求](docs/superpowers/specs/2026-09-03-work-item-control-design.md)。

## PRD 中的 draw.io ER 图

PM Agent 只在需求证据明确包含至少两个持久化实体及其关系时生成 ER 图；证据不足时 `er_diagrams` 保持为空，不为装饰而画图。PRD 正文使用仓库内固定版本的 draw.io 查看器，可拖动画布、滚轮或按钮缩放、适应、复位、全屏和下载 `.drawio`。正文组件不直接编辑节点或关系，PRD Diff 也不加载图形或 XML，只显示类似“ER 图：订单数据模型”的带版本哈希链接。

点击“在 draw.io 中编辑”会打开官方 `https://embed.diagrams.net` 编辑器。保存结果先作为浏览器本地草稿保留，再通过当前 PRD 的版本号和 commit 校验回传；有效修改会创建不可变的 `n+1` PRD，重新运行自动审核并回到人工审核/返工流程，不会自动批准或拆分任务。版本冲突、网络失败和弹窗关闭不会覆盖当前 PRD；草稿可重试或下载。外部编辑需要能访问 diagrams.net 并允许该弹窗，日常正文查看不依赖外网。

供应链版本固定如下：

- Agent 生成规则：`Agents365-ai/drawio-skill` 提交 `65f5fa0505f43d8af104d00c6087cb02c8c0e2f3`（skill 3.2.1，MIT），来源与目录哈希见 `backend/skills/drawio-skill-source.json`。
- 正文查看器：draw.io core `31.4.2`（Apache-2.0），来源与文件哈希见 `frontend/aios-main/public/drawio/31.4.2/SOURCE.json`。

升级任一版本时必须从新的固定提交/标签重新复制完整产物，同时更新许可证、来源元数据、SHA-256 和相关安全/渲染测试；运行时不会跟随上游分支自动下载。

## 本地启动

前后端是两个独立进程，需要分别占用一个 PowerShell 窗口，并让两个窗口持续运行。只启动前端、关闭后端窗口，或后端启动命令报错退出，页面都会显示 `NETWORK_ERROR`。

### 1. 配置并启动后端

在项目根目录打开第一个 PowerShell 窗口，然后运行：

```powershell
cd backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，至少确认：

```env
DATABASE_URL=sqlite:///data/gateway.db
FIRSTFLIGHT_IDENTITY_MODE=local
FIRSTFLIGHT_ACTOR_ID=owner-1
FIRSTFLIGHT_CORS_ORIGINS=http://127.0.0.1:3001,http://localhost:3001

GITEA_URL=http://localhost:3000
GITEA_TOKEN=<服务账号令牌>
GITEA_OWNER=owner-1
GITEA_REPO=docs
GITEA_BASE_BRANCH=main
```

不要把真实 Gitea token、Agent key 或 `.env` 提交到 Git。

`GITEA_TOKEN` 的示例值只是占位符，不能直接使用。启动后端前，可在项目根目录用以下 PowerShell 命令验证 token；应返回账号名称和数字 ID，而不是 `401 Unauthorized`：

```powershell
$config = @{}
Get-Content backend\.env | ForEach-Object {
  if ($_ -match '^([^#=]+)=(.*)$') { $config[$Matches[1].Trim()] = $Matches[2].Trim().Trim('"') }
}
$headers = @{ Authorization = "token $($config.GITEA_TOKEN)" }
Invoke-RestMethod -Headers $headers "$($config.GITEA_URL)/api/v1/user" |
  Select-Object id, login
```

该 token 对 `GITEA_OWNER/GITEA_REPO` 指定的仓库必须同时具有读取和写入权限，否则正文可以从数据库兜底显示，但 PRD Diff、行批注和 Gitea 评论不可用。更新 token 后必须重启后端。

首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8088 --env-file .env
```

不要关闭这个窗口。看到以下内容才表示后端进程已经启动：

```text
Uvicorn running on http://127.0.0.1:8088
```

后端代码也会自动读取 `backend/.env`；命令中的 `--env-file .env` 用于让启动行为更明确。系统环境变量优先于 `.env` 中的同名配置。

另开一个 PowerShell 窗口执行健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8088/healthz
```

看到 `status=ok` 和 `database=ok` 后，再启动前端。`http://127.0.0.1:8088/` 返回 404 是正常现象，后端没有根页面。

### 2. 配置并启动前端

健康检查通过后，在第二个 PowerShell 窗口中进入项目根目录并运行：

```powershell
cd frontend\aios-main
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
npm install
npm run dev
```

前端真实模式需要：

```env
VITE_DATA_MODE=api
VITE_API_BASE_URL=http://127.0.0.1:8088
```

打开 <http://127.0.0.1:3001/>。API 模式下后端不可用时页面会显示错误，不会偷偷回退到 Mock 数据。

修改前端 `.env` 后必须停止并重新执行 `npm run dev`，因为 Vite 只在启动时读取环境变量。

## 恢复已有 Session

如果浏览器记录丢失但后端 Session 仍存在，可在首页输入 Session ID 并点击“恢复并打开”。这个动作只读取既有状态，不会创建新的 Agent 运行。

## 常见问题

### `NETWORK_ERROR：无法连接后端`

这条错误表示浏览器无法连接 `VITE_API_BASE_URL` 指向的服务。它通常不是模型 API Key 问题，也不是 Windows 系统故障；先按下面顺序检查。

1. 确认 8088 端口确实有后端进程监听：

   ```powershell
   Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue
   ```

   没有任何输出，说明后端没有运行。回到 `backend` 目录重新执行后端启动命令，并保持该 PowerShell 窗口打开。如果命令立即退出，应先处理窗口中显示的具体错误。

2. 确认健康接口可访问：

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8088/healthz
   ```

   如果这里提示“目标计算机积极拒绝”，仍然表示后端没有在 8088 端口监听，与前端 `.env` 无关。

3. 确认前端实际使用 API 模式和相同端口：

   ```powershell
   cd frontend\aios-main
   Get-Content .env
   ```

   应包含：

   ```env
   VITE_DATA_MODE=api
   VITE_API_BASE_URL=http://127.0.0.1:8088
   ```

   修改后重新启动 `npm run dev`。还可运行 `Get-ChildItem -Force .env*`，确认 Windows 没有把文件保存成隐藏扩展名的 `.env.txt`。

4. 如果健康接口正常，但浏览器开发者工具提示 CORS 错误，检查后端 `.env`：

   ```env
   FIRSTFLIGHT_CORS_ORIGINS=http://127.0.0.1:3001,http://localhost:3001
   ```

   修改后重新启动后端。

常见的后端启动错误：

- `ModuleNotFoundError`：在 `backend` 目录执行 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。
- `Address already in use`：执行 `Get-NetTCPConnection -LocalPort 8088 -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess` 查出占用端口的进程。若改用其他后端端口，必须同步修改前端 `VITE_API_BASE_URL` 并重启前端。
- 找不到 `.env` 或数据库相对路径异常：确认命令是在 `backend` 目录内执行。直接调用 `.\.venv\Scripts\python.exe` 不依赖 PowerShell 的脚本执行策略，无需运行 `Activate.ps1`。

### 页面能打开，但动作失败

先执行 `Invoke-RestMethod http://127.0.0.1:8088/healthz`。如果正常，再检查前端 `.env` 的 `VITE_API_BASE_URL` 和后端 `FIRSTFLIGHT_CORS_ORIGINS`。

### Gitea 批注不可用

确认 Gitea 位于 `http://localhost:3000`，目标仓库及 `main` 分支已初始化，并且服务 token 对目标仓库具有读写和评审权限。批注必须落在后端返回的有效 diff 行上。

- `GITEA_UNAUTHORIZED`：`.env` 中仍是示例 token、token 填写错误或 token 已被撤销。重新创建有效 token、更新 `.env` 并重启后端。
- `GITEA_FORBIDDEN`：token 有效，但对应账号没有目标仓库的读取、写入或评审权限。
- Gitea 暂不可用时，页面仍允许阅读数据库中保存的 PRD；如果后端当前允许确认，可勾选风险提示后直接确认并进入任务拆解。Diff 和批注不会由前端伪造。

### 跳过澄清后 PRD 生成失败

跳过动作已经被安全保存，Session 会停留在 `SPECIFICATION`。刷新页面后重新执行“生成 Spec”即可，不需要重新回答或再次跳过。

### 为什么看不到真实 Agent 思考过程

WebGUI 只展示安全阶段进度和审计摘要，不展示原始推理内容。服务端保留可审计的调用状态和证据，但敏感输入输出不会直接暴露到浏览器。

## 工程文档

- 修改日志：[`CHANGELOG.md`](CHANGELOG.md)
- 本机部署记录：[`docs/local-deployment.md`](docs/local-deployment.md)
- 后端运行与 API 示例：[`backend/readme.md`](backend/readme.md)
- 后端工程框架：[`backend/gitea-prd-review-engineering-guide.md`](backend/gitea-prd-review-engineering-guide.md)
- 前端架构与 API：[`frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md`](frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md)
- 第一阶段前后端契约：[`docs/frontend-backend-integration-contract.md`](docs/frontend-backend-integration-contract.md)
