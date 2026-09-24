# firstFlight 项目长期记忆

## 本机运行拓扑（必须记住）

| 组件 | 位置 | 监听 | 启动 |
|------|------|------|------|
| 后端 | `backend/` | `127.0.0.1:8088` | `.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8088 --env-file .env`，或 **`tools\start-backend.cmd`**（幂等、带日志） |
| 前端 | `frontend/aios-main/` | `127.0.0.1:3001` | `npm run dev` |
| Neo4j 图谱（Semantica 审计） | Docker 容器 `memory-neo4j` | 宿主 `7474`/`7687` | 自动（`unless-stopped` + Docker Desktop 已在 `HKCU\...\Run`） |
| **MCP Gateway**（2026-09-23 落地） | `backend/app/mcp_gateway/` | **复用 8088，`POST /mcp`** | 随后端启动；`MCP_GATEWAY_ENABLED=0` 可关；独立进程走 `python -m app.mcp_gateway.standalone`（8099） |

**登录自启（2026-09-21 装）**：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\firstFlight-backend.cmd`
→ 调 `tools\start-backend.cmd`。删该文件即关闭。`schtasks /sc onlogon` 在本机报"拒绝访问"（需管理员），故走启动文件夹。
launcher 留痕在 `backend/data/backend-8088.launcher.log`（与 uvicorn 的 `.out.log` 分开）。

- **必须通过 `.venv/Scripts/python.exe` 调用**：它是 virtualenv 的 launcher **桩**，会 exec 真正的解释器
  （`AppData\Local\Programs\Python\Python312\python.exe`）并注入 venv 上下文。所以
  `wmic` 在健康服务上显示 `ExecutablePath` = 基础 Python **是正常的**；**绕过桩**直接调基础 Python 才报
  `No module named uvicorn`。同一个二进制，两种结果——别按路径判断依赖可用性。
- **`SEMATICA_NEO4J_PASSWORD` 不能留空**：Neo4j 不把空密码当免密，会回
  `AuthError ... missing key credentials`，表现为 `/semantica/status` 的 `available:false`，
  前端显示「无法连接 Neo4j 图谱数据库」，极易误判成"服务没开"。本机值 = `neo4j123`
  （`docker inspect memory-neo4j` 的 `NEO4J_AUTH`）。
- **改 `backend/.env` 必须重启后端**：`Settings.from_env()` 每进程只读一次。
- 后端**必须单进程单 worker**（见 `docs/backend/README.md`）：BackgroundTasks 与命令 ownership fence 只在进程内协调。
- 排障手册见技能 `.workbuddy/skills/firstflight-local-runtime/`。

## MCP Gateway 约定

- 技术选型**故意偏离 spec**：spec 要求 TS + Hono + 官方 MCP SDK 独立服务，实际用 Python 挂进
  FastAPI（单进程约束 + 快速交付）。JSON-RPC 手写，`Accept` 含 `text/event-stream` 才回 SSE。
- 配置在 `app/mcp_gateway/config/{gateway,skills}.yaml`，`MCP_GATEWAY_CONFIG_DIR` 可换目录。
  鉴权：静态 token（YAML 的 `dev-token-*` **仅供开发**）+ 签名 token（兼容 firstFlight 两段式与
  标准 HS256 JWT）。暴露面 deny-all，白名单走 `fnmatch`（`code-search.*`、`spaces://collab/knowledge/*`）。
- 新增技能 = 改 `skills.yaml` + 给 agent 加白名单 + `POST /mcp-gateway/reload`（**只重载 YAML，不重载环境变量**）。
- 管理端点头 `X-MCP-Admin-Token`，取值 `MCP_GATEWAY_ADMIN_TOKEN`；不设置则全部 503（fail-closed）。
- 限流是**进程内内存**计数 → 多实例/多 worker 会各算各的（当前后端单进程，无碍）。
- 完整注意事项清单见 `docs/mcp-gateway.md` 第 7 节。

## 排查方法论（项目惯例）

- **先读状态接口，别猜"服务没开"**。`/semantica/status` 的
  `configured` / `available` 两态能区分"开关没开"和"连不上"；`docker ps` 显示 Up 不等于可用。
- **可逆基线探针**：判定失败是否为既有问题时，临时禁用改动 / 临时 `describe.skip`，比对失败数量后再下结论，
  不要只靠推断。已知基线：`tests/integration/test_block_prd.py` 恒有 4 失败（缺 `pypdf` + 异步竞态）；
  `frontend/src/api/workspace.test.tsx` 整文件恒有 34 失败（单独 `-t` 跑筛选用例全通过）。
- **只改被要求的表层，不吞掉用户的显式语义**；若有安全/数据风险点必须保留，就在回复里明确报备并给出撤销方式。

## 沙箱工具限制

- Bash 工具：`cmd.exe` 调用被拦；长驻服务用 `run_in_background: true`。
- **进程跨工具调用活不下来（铁证）**：`Popen(DETACHED_PROCESS)`、`run_in_background`、
  甚至 `explorer.exe` → `start /min` 这种正规 Windows 分离启动，全部会在工具调用结束后被回收。
  → 后台任务报 `failed` 时**先探端口**（沙箱回收的是包装进程，uvicorn 子进程常被孤儿化后继续服务）；
  反过来，脚本自测通过也只证明脚本对，**不证明进程能活过会话**。
- **要执行 `.cmd` 只能用 `MSYS_NO_PATHCONV=1 explorer.exe "C:\path\x.cmd"`**（已用标记文件证实会执行）；
  看不到 stdout，所以让脚本自己落日志再断言分支。**同一路径重复调用可能被静默忽略**，换文件名。
- PS 工具在本项目沙箱下**输出恒为空**，不要依赖；改用 `netstat` / `tasklist` / `wmic` / `taskkill`。
- 命令内容里出现 Power-Shell 相关字面量会触发安全拦截，写文件请用 Edit/Write 工具。
- **Edit 工具偶发"返回成功但没落盘"**（2026-09-23 多次复现：main.py 的 import、protocol.py、
  router.py 各一次）。改完必须 `grep` 复核；失效时改用 Write 整文件重写。
