# MCP Gateway（自运营工具网关）

> 初版落地：2026-09-23。目标：给接入的 AI Agent（Codex / Claude Code / 平台 worker）统一暴露"已批准"的
> tool / resource / prompt，按调用者身份裁剪暴露面，代理调用后端服务，并统一做鉴权、审计、限流。

---

## 1. 与 spec 的关系：做了哪些适配

原始规格 `mcp-gateway-build-spec.md` 假定独立的 TypeScript 服务（Hono + 官方 MCP SDK）。为了与
firstFlight 现有栈对齐、并且更快交付，本实现做了以下调整（**这是最重要的取舍，请先看这里**）：

| spec 要求 | 本实现 | 原因 |
| --- | --- | --- |
| TypeScript + Hono + `@modelcontextprotocol/sdk` | **Python + FastAPI，挂载进现有后端** | 后端已是 FastAPI 单进程；新起一个 Node 进程要多占端口、多一套运维；且 `docs/backend/README.md` 要求后端必须单进程单 worker |
| 独立目录 `mcp-gateway/`，监听 8080 | `backend/app/mcp_gateway/`，**复用 8088**，端点 `POST /mcp` | 零新增部署单元；配置/密钥/审计都跟后端同源 |
| `config/` 在项目根 | `backend/app/mcp_gateway/config/`，可用 `MCP_GATEWAY_CONFIG_DIR` 指到任意目录 | 包自包含；生产可把配置挂到容器外 |
| 官方 SDK 的 transport | **手写 JSON-RPC 2.0 分发** + Streamable HTTP 语义（JSON 或 SSE 响应） | 依赖零新增；按 agent 裁剪本来就要在 handler 层做，SDK 的 server 抽象帮不上忙 |
| 单独 Dockerfile | 暂未提供（与后端同进程） | 需要隔离时用 `python -m app.mcp_gateway.standalone`（默认 8099） |

**没有实现**（明确在范围外，与 spec 一致）：技能编辑界面、OAuth 2.1 授权服务器、知识库本体。

---

## 2. 快速开始

```bash
# 1) 无凭据联调（所有适配器返回假数据）
set MCP_GATEWAY_MOCK_UPSTREAM=1
set MCP_GATEWAY_ADMIN_TOKEN=dev-admin

# 2) 起后端（网关随之挂载）
tools\start-backend.cmd

# 3) 自检
curl http://127.0.0.1:8088/mcp-gateway/healthz

# 4) 握手 + 列工具（codex-main 的固定开发 token）
curl -s http://127.0.0.1:8088/mcp \
  -H "Authorization: Bearer dev-token-codex" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

也可以用 CLI（支持 tools / call / resources / read / prompts / prompt / mint）：

```bash
.venv\Scripts\python.exe scripts/mcp_gateway_call.py tools --token dev-token-codex
.venv\Scripts\python.exe scripts/mcp_gateway_call.py call gateway_ping --token dev-token-codex --arg message=hi
.venv\Scripts\python.exe scripts/mcp_gateway_call.py mint --agent codex-main --secret <MCP_GATEWAY_JWT_SECRET>
```

MCP Inspector：`npx @modelcontextprotocol/inspector` → Transport 选 **Streamable HTTP**，
URL 填 `http://127.0.0.1:8088/mcp`，Auth 头填 `Bearer dev-token-codex`。

---

## 3. 端点

| 端点 | 说明 |
| --- | --- |
| `POST /mcp` | Streamable HTTP 主端点。`Accept: application/json` 回 JSON，`text/event-stream` 回 SSE；纯通知返回 202；`initialize` 响应带 `Mcp-Session-Id` |
| `GET /mcp` | 仅 `Accept: text/event-stream`：服务端 keep-alive 流（15s 一个注释帧）；否则 405 |
| `DELETE /mcp` | 结束会话（204）。网关本身无状态，每个请求都带完整鉴权头 |
| `GET /mcp-gateway/healthz` | 健康与配置快照（**不含**密钥） |
| `GET /mcp-gateway/agents` | 各 agent 的有效暴露面（需管理令牌） |
| `GET /mcp-gateway/skills` | 技能清单（刻意不返回上游地址与凭据） |
| `GET /mcp-gateway/audit?limit=50` | 最近的调用审计（内存环形缓冲） |
| `POST /mcp-gateway/reload` | 热重载两个 YAML（**不重载环境变量**） |
| `POST /mcp-gateway/tokens` | 给已注册 agent 铸造 token：`{"agent_id":"codex-main","ttl_seconds":43200}` |

管理端点的令牌来自请求头 `X-MCP-Admin-Token`（或 `Authorization: Bearer`），
比对环境变量 `MCP_GATEWAY_ADMIN_TOKEN`，常量时间比较。

---

## 4. 配置

两个 YAML 是单一事实来源，环境变量可覆盖：

- `backend/app/mcp_gateway/config/gateway.yaml` — 服务元数据、鉴权模式、限流、审计、**agent 注册与白名单**，以及 capability 调用路由规则
- `backend/app/mcp_gateway/config/skills.yaml` — MCP 技能清单（tool / resource / prompt + 适配器绑定）、外部 Agent Skill 来源与包清单

环境变量：

| 变量 | 作用 |
| --- | --- |
| `MCP_GATEWAY_ENABLED=0` | 启动时完全不挂载网关 |
| `MCP_GATEWAY_CONFIG_DIR` | 指向另一份配置目录 |
| `MCP_GATEWAY_JWT_SECRET` / `GATEWAY_JWT_SECRET` | 验签密钥；留空则回落到后端 `auth_jwt_secret` |
| `MCP_GATEWAY_AUTH_MODE` | `jwt` / `static` / `both` |
| `MCP_GATEWAY_STATIC_TOKENS` | `token:agentId,token2:agentId2`，避免把固定 token 写进 YAML |
| `MCP_GATEWAY_ADMIN_TOKEN` | 管理端点令牌（不设置 → 管理端点 503） |
| `MCP_GATEWAY_MOCK_UPSTREAM=1` | 所有适配器返回假数据 |
| `MCP_GATEWAY_RATE_LIMIT_PER_MINUTE` | 全局配额（被 agent 的 `rate_limit_per_minute` 覆盖） |
| `MCP_GATEWAY_AUDIT_DIR` | 审计 JSONL 目录，默认 `backend/data/mcp-gateway` |
| `IMG_GEN_URL` / `IMG_GEN_API_KEY` | 画图上游（http-json 适配器） |
| `RAG_SEARCH_URL` / `RAG_API_KEY` | 检索服务（rag-knowledge 适配器），未设则回落 `WEKNORA_BASE_URL` |
| `CODE_SEARCH_MCP_URL` / `CODE_SEARCH_TOKEN` | 外部 MCP 级联地址 |

---

## 5. 鉴权与暴露面

- 首版支持 **Bearer**：① YAML / 环境变量里的静态 token；② 签名 token（firstFlight 的 `<body>.<sig>`，
  以及标准 HS256 JWT，二者都支持）。`sub` 即 `agent_id`。
- **策略只来自配置**：token 里的 `roles` 只是信息，真正的权限是 `gateway.yaml` 里该 agent 的
  `allowed_tools / allowed_resources / allowed_prompts`。**默认 deny-all**：没注册的 agent 直接 -32001。
- 匹配用 `fnmatch`，支持通配符：`code-search.*` 命中级联工具，`spaces://collab/knowledge/*` 命中资源 URI。
- `initialize` 与 `ping` 不需要鉴权（协议握手）；其余方法每个请求都验。
- 上游凭据只在适配器里从环境变量读取，**绝不下发**给 agent（审计里也做了脱敏）。

错误码：

| code | 含义 |
| --- | --- |
| `-32000` | 未认证（缺/坏 token） |
| `-32001` | 未授权（agent 无权访问该项） |
| `-32002` | 参数校验失败（`data.errors` 有明细） |
| `-32003` | 上游不可用 / 未配置 |
| `-32004` | 超出配额（`data.retry_after`） |
| `-32005` | 工具/资源/提示词不存在 |

---

## 6. 适配器契约与新增技能

`app/mcp_gateway/adapters/base.py` 定义统一接口（`call_tool` / `list_resources` / `read_resource` /
`dynamic_tools`）。现有适配器：`builtin`（gateway_ping、gateway_whoami）、`http-json`（画图，
含异步任务轮询与图片提取）、`rag-knowledge`（检索 + `spaces://` 资源）、`external-mcp`（级联）。

新增一个技能只需两步：

1. 在 `config/skills.yaml` 加一条（写明 `kind` / `input_schema` / `adapter` / `*_env`）；
2. 在 `gateway.yaml` 里给需要的 agent 加白名单；然后 `POST /mcp-gateway/reload`。

级联（external-mcp）工具对外命名为 `{skill_id}.{upstream_tool}`（如 `code-search.search_repo`），
白名单写 `code-search.*` 即可放行。

### 外部 Agent Skill 包与单次调用分流

附件《MCP Gateway Skill 分流完整表》的 22 个场景同时列有真正的 Agent Skill、
软件项目、标准和自建方法。`skill_packages` 只收录已在上游固定提交中核实存在
`SKILL.md` 且许可证允许本地保留的 56 个包，按 `scenarios` 关联场景；其余条目没有冒充已安装包。
Anthropic 的 `docx`、`pptx`、`xlsx` 包带有禁止在其服务外保留副本的许可条款，
因此未加入安装清单。
这些 Agent Skill 是工作流指令包，与上方 MCP `skills` 的 tool/resource/prompt 注册项不同。

`gateway.yaml` 中的 `capability_routing.rules` 按每次调用的元数据顺序决定：
人工门禁、调度、发布门禁与审批走 `ORCH`；远端资源、凭据和重算力走 `REMOTE`；
本地仓库、文件及 localhost 走 `LOCAL`。调用者需要提供 `resource_scope`、
`credential_class`、`compute_class`、`governance`、`latency_sensitivity` 和
`control_type`；缺失或非法值返回 `ORCH`，避免猜测。`route_capability` 工具只给
`codex-main` 开放，返回路由和命中的规则，**它只做决策，不代理执行，也不替代
ORCH 的人工批准或发布门禁**。外部 Skill 包本身没有固定的 LOCAL/REMOTE 位置。

安装脚本默认仅离线检查清单，必须显式指定 `--install` 才会联网下载：

```powershell
cd backend
.venv\Scripts\python.exe scripts\install_gateway_skills.py
.venv\Scripts\python.exe scripts\install_gateway_skills.py --install
```

脚本按 `skill_sources` 的固定 Git commit 拉取、检查 `SKILL.md` 和许可证、拒绝符号链接，
把包与来源/校验记录放在 `backend/skills/gateway_packages/<id>/`。已存在且校验通过的包会跳过；
校验不一致的包会报错，不覆盖。脚本不运行下载包的脚本，也不自动加入结构化 Agent
运行时的可信 Skill 白名单。安装后，`codex-main` 可通过 MCP `resources/list`、
`resources/read` 读取 `skill://<id>`；读取时再次核对来源和目录校验值。

---

## 7. ⚠️ 上线前必须处理的注意事项

按优先级排列：

1. **删掉开发用静态 token**。`config/gateway.yaml` 里的 `dev-token-*` 是明文固定凭据，任何人拿到就能
   以对应 agent 身份调用。生产请删除 `static_tokens` 段，改走 `MCP_GATEWAY_STATIC_TOKENS` 或签名 token。
2. **必须设置 `MCP_GATEWAY_ADMIN_TOKEN`**，否则管理端点 503（fail-closed 设计）。反过来：只要设了它，
   拿到该令牌就等于能看全量 agent/skill/审计，请按密钥管理。
3. **改 `.env` 必须重启后端**：`Settings.from_env()` 每进程只读一次（既有约定）。`POST /mcp-gateway/reload`
   只重载 YAML，不重载环境变量。
4. **限流是进程内内存计数**。后端本来就要求单进程单 worker，一旦横向扩容或多 worker，配额会各算各的。
   需要多实例时把 `SlidingWindowLimiter.check` 换成 Redis 实现（接口保持不变）。
5. **审计日志按天切分但不会自动清理**：`backend/data/mcp-gateway/audit-YYYY-MM-DD.jsonl` 会持续增长，
   请自行加轮转/归档。脱敏规则见 `audit.py`：字段名匹配 `api_key|token|secret|password|authorization|credential|cookie|signature`
   的值直接替换为 `***redacted***`，其余字符串只截断前 200 字符。
6. **没有吊销机制**：token 一旦签发，在其 `exp` 之前都有效。要立刻失效只能改签名密钥（会让所有 token 失效）
   或从静态表里删除并重启。
7. **上游未配置时工具会返回 -32003**，而不是从列表里消失。要么配好 `IMG_GEN_URL` / `RAG_SEARCH_URL`，
   要么用 `MCP_GATEWAY_MOCK_UPSTREAM=1`；也可以把该技能的 `enabled: false`。
8. **级联工具会放大 `tools/list` 的延迟**：外部 MCP 上游不可达时该技能的级联工具会被静默省略（降级），
   目录结果有 60s 缓存（`cache_ttl_sec`），上游变更后最多滞后一个 TTL。
9. **图片走 base64**：>5MB（`max_image_bytes`）会自动退化为返回 URL 文本。大图建议直接调对象存储。
10. **协议版本固定 `2025-06-18`**，兼容声明 `2025-03-26` / `2024-11-05` 的客户端会按其请求版本回握。
11. **GET /mcp 的 SSE 流只是 keep-alive**，没有实现服务端主动推送通知（`notifications/*` 由客户端 POST）。
12. 已知的**既有基线失败**（与本次改动无关）：`tests/unit/test_agent_gateway.py::
    test_prd_nodes_require_evidence_driven_drawio_behavior`、`tests/unit/test_ai_sentence_stream.py::
    test_line_range_accepts_neighbouring_changes_but_rejects_distant_changes`。

---

## 8. 测试与文件清单

```bash
.venv\Scripts\python.exe -m pytest tests/unit/test_mcp_gateway.py tests/unit/test_mcp_gateway_policy.py -q
```

覆盖：配置加载、静态/JWT 鉴权、deny-all 裁剪、级联通配符、限流窗口、审计脱敏与落盘、
握手、`tools/list` 按 agent 差异、越权 `-32001`、参数校验 `-32002`、资源读与越权、提示词展开、
SSE 响应、通知 202、管理端点门禁。

新增/修改文件：

- `backend/app/mcp_gateway/`（config / errors / auth / exposure / ratelimit / audit / protocol / router / standalone / adapters/ / config/*.yaml）
- `backend/main.py`（挂载 + `MCP_GATEWAY_ENABLED` 开关）
- `backend/tests/unit/test_mcp_gateway.py`、`test_mcp_gateway_policy.py`
- `backend/scripts/mcp_gateway_call.py`（CLI）、`backend/.env.example`（新增变量段）、本文档
