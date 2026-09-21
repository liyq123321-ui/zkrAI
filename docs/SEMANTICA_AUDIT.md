# Semantica 审计层（Agent 行为记录与溯源）

本仓库通过 [Semantica](https://github.com/semantica-agi/semantica)（`semantica` on PyPI，MIT）
为 **整个项目的 agent 行为** 建立可审计、可溯源、可扩展的记录层。

- 每次 agent 运行事件（思考、工具调用、文件变更、MCP 调用、结构化输出…）在写入既有
  SQL `AgentRuntimeEvent` 的同时，被镜像进 Semantica 的 **上下文图谱（Context Graph）**。
- 每次 LLM 调用（`CodexProvider._call`）额外记录一条 **决策节点（Decision）**，
  带 reasoning / outcome / confidence，并通过 `HAS_DECISION` / `WAS_GENERATED_BY` 边挂到对应 `AgentCall`，通过 `WAS_ATTRIBUTED_TO` 挂到系统 Agent（`SoftwareAgent`）。
- 图谱自带 **W3C PROV-O 溯源**，支持决策链回溯、相似决策检索、影响分析、策略合规检查。
- 存储后端为 **Neo4j 属性图**（可零代码切换 Oxigraph / 其他后端）。
- 对外暴露 **MCP server + REST Explorer**，供未来的 agent / 审计 / 合规工具查询。
- **默认开启**（`SEMATICA_ENABLED=true`）：每条 agent 运行事件与 LLM 决策都会镜像进 Neo4j 属性图；Neo4j 不可达时经熔断器自动降级为 no-op，绝不阻塞主流程。

---

## 架构与数据流

```
Agent 运行
  └─ app/agents/codex.py (CodexStructuredRunner)
       ├─ 发出 runtime event ──► AgentRuntimeEventStore.append()
       │                            ├─ SQL: AgentCall / AgentRuntimeEvent   (既有，保留)
       │                            └─ SemanticaRecorder.record_runtime_event()  ──► Neo4j 图节点
       └─ CodexProvider._call()  ──► SemanticaRecorder.record_llm_decision()   ──► Decision 节点 + 因果边
```

图谱节点类型：`Project`、`AgentSession`、`AgentCall`、`AgentEvent`、`Decision`（`AgentDecision`）、`SoftwareAgent`。
核心关系：`Project -[:HAS_SESSION]-> AgentSession -[:HAS_CALL]-> AgentCall -[:EMITTED]-> AgentEvent`、`AgentCall -[:HAS_DECISION]-> Decision`、`Decision -[:WAS_ATTRIBUTED_TO]-> SoftwareAgent`，以及 `WAS_GENERATED_BY` / `WAS_ASSOCIATED_WITH` 溯源边。所有节点/边带类型与属性（时间戳、operation、confidence 等），可用 Cypher 多跳回溯（见下）。

---

## 配置（环境变量，均在 `app/config.py` 的 `Settings` 中读取）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SEMATICA_ENABLED` | `true` | 是否启用审计层（默认开启；Neo4j 不可达时自动熔断降级为 no-op） |
| `SEMATICA_BACKEND` | `neo4j` | 存储后端：`neo4j` / `oxigraph` / `memory` |
| `SEMATICA_NEO4J_URI` | `bolt://localhost:7687` | Neo4j bolt 地址 |
| `SEMATICA_NEO4J_USER` | `neo4j` | Neo4j 用户名 |
| `SEMATICA_NEO4J_PASSWORD` | （空） | Neo4j 密码。**必须填**，留空会被 Neo4j 直接拒绝认证（见下方排查） |
| `SEMATICA_NEO4J_DATABASE` | `neo4j` | Neo4j 数据库名 |
| `SEMATICA_GRAPH_NAME` | `firstflight` | 图谱命名空间，便于多项目隔离 |
| `SEMATICA_SERVER_ENABLED` | `false` | 是否由本进程启动 REST Explorer |
| `SEMATICA_SERVER_HOST` | `127.0.0.1` | REST Explorer 绑定地址 |
| `SEMATICA_SERVER_PORT` | `8000` | REST Explorer 端口（上游 `semantica-server` 亦默认 8000；本启动器经 uvicorn 透传此值） |
| `SEMATICA_ALLOW_ANONYMOUS` | `false` | 本地开发免鉴权（生产务必 `false`） |
| `SEMATICA_API_KEY` | （空） | REST/MCP 鉴权密钥 |

> 既有 SQL 事件表 **完全保留**，Semantica 是附加的、更结构化的审计层，不替代原有存储。

---

## WebGUI 审计 Tab 集成（按项目查看 agent 审计历史）

审计 tab（`AgentRuntimePanel`，对应导航语“打开审计记录”）**按项目**展示 agent 行为审计历史：

- 运行记录（Codex JSONL 事件、推理摘要、工具调用、文件变更等）来自既有 SQL `agent_runtime_events`，无需 Neo4j 即可查看。
- Semantica 在该 tab 中作为**决策溯源**层接入：打开任一 agent 的运行记录弹窗，底部“**Semantica 决策溯源**”区块展示该会话在 Neo4j PROV 图中捕获的 LLM 决策（推理、结果契约、置信度、决策方）。
- tab 标题旁有一个**状态徽标**：`已启用 · Neo4j 已连接` / `已启用 · Neo4j 未连接` / `未启用`，反映 Semantica 审计层的实时健康度。

### 后端只读接口

| 端点 | 说明 |
|------|------|
| `GET /semantica/status` | 审计层健康快照（`configured` / `available` / `backend` / `graph_name` / `neo4j_uri`） |
| `GET /semantica/decisions?project_id=<id>&agent_session_id=<可选>` | 某项目（可选某一 agent 会话）的 LLM 决策列表，按时间倒序 |

`status.available` 是**真实连通性**：后端会对图谱做一次轻量单节点读探测（结果缓存 30s，`STATUS_PROBE_TTL_SECONDS`），因此“配置已开但 Neo4j 没起”会如实返回 `available:false`，而不是只看开关的假阳性。

`/decisions` 用 `available` + `reason` 区分两种失败：`reason="disabled"`（审计层未启用）与 `reason="unreachable"`（Neo4j 连接失败）。这两个接口都做了**优雅降级**，Semantica 未启用或 Neo4j 不可达时返回 `available:false` 而非报错，tab 上对应显示“未启用 / 无法连接”提示。

> 要让“决策溯源”真正有数据，需要本地/可达的 Neo4j（见下“启动 Neo4j”）。只要 `SEMATICA_ENABLED=true` 且 Neo4j 可达，所有 agent 调用都会自动写入决策节点，无需改代码。

---

## 启动 Neo4j（一次性，推荐 Docker）

```bash
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j=<你的密码> \
  neo4j:5
```

然后在 `backend/.env` 中：

```
SEMATICA_ENABLED=true
SEMATICA_BACKEND=neo4j
SEMATICA_NEO4J_URI=bolt://localhost:7687
SEMATICA_NEO4J_USER=neo4j
SEMATICA_NEO4J_PASSWORD=<你的密码>
SEMATICA_GRAPH_NAME=firstflight
```

无需重启应用即可生效（recorder 在每次写入时按最新设置惰性建连；连不上会自动降级为 no-op 并进入冷却）。

---

## 启用 MCP server + REST Explorer（未来可扩展性）

Semantica 在 0.6.8 暴露两个可扩展面：

- **REST Explorer（实时）**：一个 FastAPI 服务，读取**同一个 Neo4j 图谱**，提供图浏览、决策、
  溯源（PROV）、SPARQL 等 API。这是审计 / 合规人员的主要入口。
- **MCP server（stdio）**：`semantica-mcp` 是一个**标准输入/输出（stdio）JSON-RPC** 服务
  （0.6.8 无 HTTP transport），由 MCP 客户端自行拉起。未来的 agent / 工具链可借此直接查询
  “过去做过什么决策、为何这么做、影响了什么”。

### REST Explorer（可视化 / 审计回溯）

本项目提供受 `.env` 控制的启动器（随后端一起拉起）：

```bash
# 校验配置（不启动）
python tools/run_semantica_server.py --check

# 启动实时 REST Explorer（默认 127.0.0.1:8000，读取 Neo4j）
python tools/run_semantica_server.py
```

等效的上游命令是 `semantica-server`（同样绑定 8000，该端口在上游为硬编码；本启动器经 uvicorn
透传 `SEMATICA_SERVER_PORT`）。

> 鉴权：设置 `SEMANTICA_API_KEY` 后受保护路由需带 `X-API-Key`；设 `SEMANTICA_ALLOW_ANONYMOUS=true`
> 则免鉴权（仅限本机 loopback，切勿对外暴露）。两者都不设时，受保护路由返回 503。

主要端点：`/api/info`、`/health`、Explorer API（`/graph`、`/decisions`、`/provenance`、`/sparql`）。

### 离线快照查看（可选）

`semantica-explorer` 从一份 **ContextGraph JSON 导出**加载图谱并起一个本地看板（适合把某次
审计快照发给他人离线查看，不连活库）：

```bash
semantica-explorer --graph audit_snapshot.json --port 8000 --no-browser
```

### MCP server（供 agent 工具链查询审计图）

`semantica-mcp` 是 stdio 服务，由 MCP 客户端拉起。先用本启动器生成注册块：

```bash
python tools/run_semantica_server.py --print-mcp-config
```

把输出并入你的 MCP 客户端配置（Claude Desktop / Cline / 自建 agent 等）的 `mcpServers` 即可，例如：

```json
{
  "mcpServers": {
    "firstflight-semantica": {
      "command": "backend/.venv/Scripts/semantica-mcp.exe",
      "env": {
        "GRAPH_STORE_DEFAULT_BACKEND": "neo4j",
        "GRAPH_STORE_NEO4J_URI": "bolt://localhost:7687",
        "GRAPH_STORE_NEO4J_USER": "neo4j",
        "GRAPH_STORE_NEO4J_PASSWORD": "<你的密码>",
        "GRAPH_STORE_NEO4J_DATABASE": "neo4j",
        "SEMANTICA_DISABLE_PROGRESS": "1"
      }
    }
  }
}
```

> MCP server 与后端 recorder 共享同一个 Neo4j，因此客户端可实时回溯其它 agent 已写入的决策与事件。

---

## 查询示例（审计 / 合规）

图谱即 Neo4j 属性图，审计 / 合规可直接用 Cypher 或 REST Explorer 查询。下面示例均基于本集成
实际写入的标签（`Project` / `AgentSession` / `AgentCall` / `AgentEvent` / `Decision` /
`SoftwareAgent`）与关系（`HAS_SESSION` / `HAS_CALL` / `EMITTED` / `HAS_DECISION` /
`WAS_GENERATED_BY` / `WAS_ATTRIBUTED_TO` / `WAS_ASSOCIATED_WITH`）。

```cypher
-- 1) 某 project 下最近 50 条 agent 事件
MATCH (p:Project {project_id:$pid})-[:HAS_SESSION]->(:AgentSession)
      -[:HAS_CALL]->(c:AgentCall)-[:EMITTED]->(e:AgentEvent)
RETURN e ORDER BY e.ts DESC LIMIT 50;

-- 2) 某次 LLM 调用（AgentCall）产生的决策节点
MATCH (c:AgentCall {agent_call_id:$call})-[r:HAS_DECISION]->(d:Decision:AgentDecision)
RETURN d.decision_id, d.scenario, d.outcome, d.confidence, d.reasoning;

-- 3) 决策影响图：决策 -> 生成它的调用 -> 该调用产生的事件
MATCH (d:Decision)-[:WAS_GENERATED_BY]->(c:AgentCall)-[:EMITTED]->(e:AgentEvent)
RETURN d, c, e;

-- 4) 某系统 agent 的全部决策（按 confidence 升序，低置信度优先审查）
MATCH (a:SoftwareAgent {agent_id:"firstflight-ai"})<-[:WAS_ATTRIBUTED_TO]-(d:Decision)
RETURN d ORDER BY d.confidence ASC;
```

也可通过 REST Explorer 的 `GET /decisions`、`GET /provenance`、`GET /graph`、或 `POST /sparql`
以 HTTP/JSON 形式查询，便于接入看板与告警。

---

## 优雅降级（生产安全）

- `SEMATICA_ENABLED=false` 或 Neo4j 不可达时，recorder 为 **no-op**，绝不阻塞或拖慢 PRD 生成。
- 首次连接失败会进入 **冷却窗口**（默认 5 分钟），期间跳过写入，避免每次事件都去试探连接。
- 所有写入均在 `try/except` 中；即便审计层异常，既有 SQL 事件持久化不受影响。
- 读接口的探测失败**不触发**熔断（只做只读观测），所以“Neo4j 暂时没起”不会连累后续恢复后的写入。

---

## 排查：面板显示「无法连接 Neo4j 图谱数据库」

先看后端状态接口，它区分「没开」和「连不上」：

```bash
curl -s http://127.0.0.1:8088/semantica/status
# {"configured":true,"available":false,...}  → 开关没问题，是连不上/认证失败
# {"configured":false,...}                   → SEMATICA_ENABLED 为 false
```

`available:false` 时按顺序排除：

1. **密码没填（最常见）**。Neo4j 对空密码**不会**当成“免密”，而是直接回
   `AuthError ... Unsupported authentication token, missing key 'credentials'`。
   此时 Neo4j 进程明明是 `Up` 的，`/semantica/status` 里 `configured` 也是 `true`，
   只有 `available` 是 `false`，极易误判成“服务没开”。本机 Docker 容器
   `memory-neo4j` 的密码在 `NEO4J_AUTH` 里：
   ```bash
   docker inspect memory-neo4j --format '{{range .Config.Env}}{{println .}}{{end}}' | grep NEO4J_AUTH
   ```
2. **bolt 端口不通**：`curl` 探不到 bolt，用驱动自证更直接：
   ```bash
   .venv/Scripts/python.exe -c "
   from neo4j import GraphDatabase
   d = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j','<密码>'))
   d.verify_connectivity(); print('bolt ok'); d.close()"
   ```
3. **改了 `.env` 必须重启后端**：`Settings` 在进程启动时读一次环境变量（uvicorn
   `--env-file .env`），运行中改文件不会生效。

### 本机默认拓扑

| 组件 | 位置 | 说明 |
|------|------|------|
| Neo4j 图谱 | Docker 容器 `memory-neo4j` | 宿主 7474 / 7687，`NEO4J_AUTH=neo4j/neo4j123`，重启策略 `unless-stopped` |
| 后端 | `backend/`，127.0.0.1:8088 | `.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8088 --env-file .env` |
| 前端 | `frontend/aios-main/`，127.0.0.1:3001 | `npm run dev` |

注意 `memory-neo4j` 是其它 stack 共用的容器。**隔离只靠节点属性**，不靠库/标签：
`SEMATICA_GRAPH_NAME` 当前**没有被 `_build_graph()` 使用**（store 只用 `database` 建连），
节点直接落在 `neo4j` 库里，标签是通用的 `Project` / `AgentSession` / `Decision` 等。
所以若将来别的服务复用同一容器且也用这些标签，就会串在一起——届时应把
`SEMATICA_NEO4J_DATABASE` 指到独立数据库（`CREATE DATABASE firstflight`）。
按项目清理时用：

```cypher
MATCH (n) WHERE n.project_id = $project_id DETACH DELETE n;
```

---

## 测试

`tests/test_audit_recorder.py` 覆盖（10 项）：
- 在不连接 Neo4j 时 recorder 安全降级（`enabled=False`，调用不抛异常）。
- 给定伪 graph 时，runtime event 镜像与 LLM decision 记录生成正确的节点 / 边 / 因果关系。
- `query_decisions` 按 `project_id` / `agent_session_id` 过滤、按时间倒序；`strict=True` 时把连接错误抛出（供 `/decisions` 区分 `unreachable`）。
- `status.available` 反映真实连通性，探测结果按 TTL 缓存。
- 失败后熔断器打开（`enabled=False`）。

前端 `src/api/AgentRuntimePanel.test.tsx`（12 项）覆盖审计 tab 的按项目展示、agent 弹窗、以及 Semantica 徽标的“已连接 / 未连接 / 未启用”三态。

```bash
.venv/Scripts/python.exe -m pytest tests/test_audit_recorder.py -q
```

```bash
cd frontend/aios-main && ./node_modules/.bin/vitest run src/api/AgentRuntimePanel.test.tsx
```
