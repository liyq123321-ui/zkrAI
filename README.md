# firstFlight 用户指南

firstFlight 第一阶段是一个本地运行的需求澄清、PRD 人工审核和任务拆解工作台。用户提交项目需求后，系统会通过 Agent 判断是否需要澄清；需求明确后生成 PRD，并把 PRD 绑定到主 WorkItem 和 Gitea 行批注流程。PRD 获得人工确认后，后端生成只读的子 WorkItem 与对应 Agent Spec。

当前阶段不会执行子 WorkItem、修改代码或部署应用。页面显示的 WorkItem 与 Agent Spec 是规划产物。

## 当前完整流程

1. 在 WebGUI 填写项目目标、范围、约束和验收标准，创建 Session。
2. 如果后端返回待澄清问题，可以填写答案并继续多轮澄清。
3. 如果不希望继续澄清，可以：
   - 点击“跳过澄清并生成 PRD”；或
   - 在澄清框输入明确指令，例如“跳过澄清”“不用澄清”“直接生成 PRD”。
4. 跳过后，Agent 使用保守且合理的假设补齐模糊地带，并在 PRD 中列出重要假设供人工修改。
   如果已有 PRD 且审核再次要求澄清，可以点击“提交澄清”下方的“跳过澄清，确认当前 PRD 并拆分子任务”。该按钮会记录人工确认，直接按当前 PRD 拆分子任务；不会生成新版 PRD。若拆解失败，已确认的 PRD 会保留，可以继续重试拆解。
5. 点击主 WorkItem 卡片打开 PRD；可在有效 Gitea diff 行添加批注、回复或解决批注。
6. 有未解决批注时，确认操作会发布本轮审核并异步生成新版 PRD；没有批注时，确认操作会批准当前 PRD。
7. PRD 批准后，系统自动拆解子 WorkItem 与 Agent Spec。点击子卡片即可查看该角色收到的任务规格。

所有页面动作均以后端返回的 `legal_actions` 为准。网络中断或并发更新时，页面会刷新 Session 状态，不会在浏览器中猜测工作流状态。

## 本地启动

### 1. 配置并启动后端

在 PowerShell 中运行：

```powershell
cd backend
Copy-Item .env.example .env
```

编辑 `.env`，至少确认：

```env
DATABASE_URL=sqlite:///data/gateway.db
FIRSTFLIGHT_IDENTITY_MODE=local
FIRSTFLIGHT_ACTOR_ID=owner-1
FIRSTFLIGHT_CORS_ORIGINS=http://127.0.0.1:3001,http://localhost:3001

GITEA_URL=http://localhost:3000
GITEA_TOKEN=<服务账号令牌>
GITEA_OWNER=zkr
GITEA_REPO=docs
GITEA_BASE_BRANCH=main
```

不要把真实 Gitea token、Agent key 或 `.env` 提交到 Git。

首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8088
```

访问 `http://127.0.0.1:8088/healthz`，看到 `status=ok` 和 `database=ok` 表示后端就绪。`http://127.0.0.1:8088/` 返回 404 是正常现象，后端没有根页面。

### 2. 配置并启动前端

另开一个 PowerShell 窗口：

```powershell
cd frontend\aios-main
Copy-Item .env.example .env
npm install
npm run dev
```

前端真实模式需要：

```env
VITE_DATA_MODE=api
VITE_API_BASE_URL=http://127.0.0.1:8088
```

打开 <http://127.0.0.1:3001/>。API 模式下后端不可用时页面会显示错误，不会偷偷回退到 Mock 数据。

## 恢复已有 Session

如果浏览器记录丢失但后端 Session 仍存在，可在首页输入 Session ID 并点击“恢复并打开”。这个动作只读取既有状态，不会创建新的 Agent 运行。

## 常见问题

### 页面能打开，但动作失败

先检查 `http://127.0.0.1:8088/healthz`。如果正常，再检查前端 `.env` 的 `VITE_API_BASE_URL` 和后端 `FIRSTFLIGHT_CORS_ORIGINS`。

### Gitea 批注不可用

确认 Gitea 位于 `http://localhost:3000`，目标仓库及 `main` 分支已初始化，并且服务 token 对目标仓库具有读写和评审权限。批注必须落在后端返回的有效 diff 行上。

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
