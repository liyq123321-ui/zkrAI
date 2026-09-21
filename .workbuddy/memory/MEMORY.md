# firstFlight 项目长期记忆

## 本机运行拓扑（必须记住）

| 组件 | 位置 | 监听 | 启动 |
|------|------|------|------|
| 后端 | `backend/` | `127.0.0.1:8088` | `.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8088 --env-file .env` |
| 前端 | `frontend/aios-main/` | `127.0.0.1:3001` | `npm run dev` |
| Neo4j 图谱（Semantica 审计） | Docker 容器 `memory-neo4j` | 宿主 `7474`/`7687` | 自动（`unless-stopped`） |

- **必须用 `backend/.venv/Scripts/python.exe`**：基础 Python 3.12（`AppData\Local\Programs\Python\Python312`）
  没装 uvicorn，直接跑会 `No module named uvicorn`。
- **`SEMATICA_NEO4J_PASSWORD` 不能留空**：Neo4j 不把空密码当免密，会回
  `AuthError ... missing key credentials`，表现为 `/semantica/status` 的 `available:false`，
  前端显示「无法连接 Neo4j 图谱数据库」，极易误判成"服务没开"。本机值 = `neo4j123`
  （`docker inspect memory-neo4j` 的 `NEO4J_AUTH`）。
- **改 `backend/.env` 必须重启后端**：`Settings.from_env()` 每进程只读一次。
- 后端**必须单进程单 worker**（见 `docs/backend/README.md`）：BackgroundTasks 与命令 ownership fence 只在进程内协调。
- 排障手册见技能 `.workbuddy/skills/firstflight-local-runtime/`。

## 排查方法论（项目惯例）

- **先读状态接口，别猜"服务没开"**。`/semantica/status` 的
  `configured` / `available` 两态能区分"开关没开"和"连不上"；`docker ps` 显示 Up 不等于可用。
- **可逆基线探针**：判定失败是否为既有问题时，临时禁用改动 / 临时 `describe.skip`，比对失败数量后再下结论，
  不要只靠推断。已知基线：`tests/integration/test_block_prd.py` 恒有 4 失败（缺 `pypdf` + 异步竞态）；
  `frontend/src/api/workspace.test.tsx` 整文件恒有 34 失败（单独 `-t` 跑筛选用例全通过）。
- **只改被要求的表层，不吞掉用户的显式语义**；若有安全/数据风险点必须保留，就在回复里明确报备并给出撤销方式。

## 沙箱工具限制

- Bash 工具：`cmd.exe` 调用被拦；长驻服务必须用 `run_in_background: true`，
  `Popen(DETACHED_PROCESS)` 拉起的进程会在工具调用结束后被回收。
- PowerShell 工具在本项目沙箱下**输出恒为空**，不要依赖；改用 `netstat` / `tasklist` / `wmic` / `taskkill`。
- 命令内容里出现 Power-Shell 相关字面量会触发安全拦截，写文件请用 Edit/Write 工具。
