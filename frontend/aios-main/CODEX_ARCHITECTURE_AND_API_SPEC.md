# CodeX Agent 敏捷编排与工程研发平台：全量系统架构与源码级实现规范 (Developer & Backend Handover Spec)

> **当前实现状态（2026-09-02）**：本文后续主体最初用于描述 Mock 原型。真实第一阶段实现以 `src/api/` 和 `docs/openapi.json` 为准。使用 `VITE_DATA_MODE=api` 与 `VITE_API_BASE_URL=http://127.0.0.1:8088` 启动真实模式；该模式动态隔离 `MockApp.tsx`，只调用现有 FastAPI `/sessions`、`/prd`、`/tasks` 与 `/chat`，后端不可用时不会回退到 Mock。本文提到但 OpenAPI 中不存在的 `/v1/*`、代码执行、部署、提交浏览和原始 thought chain 均仍是未来范围。
>
> 当前真实 UI 已支持 Session 创建/恢复、多轮澄清、显式跳过澄清并自动生成 PRD、由 `legal_actions` 驱动的 Spec 审核、主 WorkItem PRD 入口、子 WorkItem Agent Spec 展示、历史版本创建 n+1、Gitea diff 行批注/回复/解决、异步 PRD 发布恢复，以及安全 Agent 阶段摘要。浏览器不提交可信 `actor_id`，身份由后端注入。

> **文档性质**: 工程实施标准 / 源码级架构解读 / 后端与 CodeX 编排引擎对接指南  
> **文档版本**: `v2.1.0-REAL-INTEGRATION`
> **前端技术栈**: 真实模式使用 React 19 + TypeScript 5.8 + Vite 6 + Vitest + Tailwind CSS 4 + Lucide Icons；Google Drive 与 Firebase 仅属于隔离的旧 Mock 功能
> **目标读者**: CodeX 后端工程师、Agent 核心架构师、全栈研发与运维测试团队  

---

## 目录
0. [当前真实前后端实现](#0-当前真实前后端实现)
1. [系统总体架构与数据流转拓扑](#1-系统总体架构与数据流转拓扑)
2. [源码级工程目录结构与职责解耦](#2-源码级工程目录结构与职责解耦)
3. [核心数据模型与 TypeScript 类型体系设计 (`src/types.ts`)](#3-核心数据模型与-typescript-类型体系设计-srctypests)
4. [核心功能模块源码级深度解析与逻辑关系](#4-核心功能模块源码级深度解析与逻辑关系)
   - 4.1 根应用容器与全局状态中心 (`src/App.tsx`)
   - 4.2 状态机约束引擎与自动化任务分解器 (`src/utils/workflowEngine.ts`)
   - 4.3 敏捷泳道看板与多维过滤视图 (`src/components/KanbanBoard.tsx`)
   - 4.4 DAG 任务依赖与执行拓扑网络 (`src/components/TaskFlowDiagram.tsx`)
   - 4.5 Gitea PRD 规格文档多点逐段批注与演进系统 (`src/components/PRDDocumentModal.tsx`)
   - 4.6 多 Agent 协同流式对话面板 (`src/components/AgentChat.tsx`)
   - 4.7 工单全生命周期审计与历史详情抽屉 (`src/components/WorkItemDetailModal.tsx`)
   - 4.8 代码工作区与 IDE 源码实时查看器 (`src/components/CodeWorkspaceTab.tsx`)
   - 4.9 Git / Gitea 自动化提交追溯流 (`src/components/CommitHistoryTab.tsx`)
   - 4.10 Google Drive 全量源码云端同步引擎 (`src/services/` & `src/components/GoogleDriveModal.tsx`)
   - 4.11 GitHub Light / Dark 双主题引擎 (`src/context/ThemeContext.tsx` & `src/index.css`)
5. [组件间协作与逻辑关系调用图 (Component Interaction Flow)](#5-组件间协作与逻辑关系调用图)
6. [CodeX 后端标准接口契约规范 (RESTful / SSE / WebSocket)](#6-codex-后端标准接口契约规范)
7. [后端对接与工程落地建议 (Best Practices)](#7-后端对接与工程落地建议)

---

## 0. 当前真实前后端实现

### 0.1 模式边界

`src/main.tsx` 根据 `VITE_DATA_MODE` 动态选择根应用：

- `api`：加载 `src/api/ApiWorkspace.tsx`，所有业务事实来自 FastAPI；失败时明确报错，不回退 Mock。
- `mock`：加载旧 `MockApp.tsx`，仅用于原型演示，不能作为接口或业务状态的证据。

真实模式的浏览器配置只有公开地址和非可信显示提示。Gitea token、Agent provider 配置和可信 `actor_id` 均留在后端。

### 0.2 真实模块结构

```text
src/api/
├── ApiWorkspace.tsx       # Session 工作区、只读 Kanban、动作编排和安全进度
├── PrdReviewPanel.tsx     # 主 WorkItem 的 PRD、Gitea 行批注和异步发布恢复
├── client.ts              # fetch、超时、统一 JSON/204/error 处理
├── config.ts              # api/mock 模式与 Base URL 校验
├── dto.ts                 # 与 OpenAPI 对齐的前端 DTO 和 CommandAction
├── errors.ts              # 网络与业务错误归一化
├── sessions.ts            # /sessions 状态、Spec、WorkItem、Agent Spec、事件
├── prd.ts                 # /prd、评论、有效 diff 行和 /tasks
├── chat.ts                # 兼容 /chat 阶段流
├── workflowUi.ts          # 纯 UI 决策：动作位置、恢复、卡片语义、跳过意图
├── client.test.ts         # HTTP 客户端合同测试
└── workflowUi.test.ts     # 工作流展示和明确跳过意图测试
```

### 0.3 当前用户流程

```text
创建/恢复 Session
  -> NEED_CLARIFICATION
       -> 提交答案(message)，可多轮
       -> 跳过澄清(skip_clarification)
            -> 自动 create_spec
  -> 主 WorkItem 卡片打开 PRD
       -> 有批注：发布审核并等待异步任务
       -> 无批注：approve
  -> convert_to_work_item
  -> 点击子 WorkItem 查看 Agent Spec
```

跳过澄清有两个入口：按钮“跳过澄清并生成 PRD”，以及澄清框中的明确短语（例如“跳过澄清”“不用澄清”“直接生成 PRD”）。短语识别只负责把明确意图映射为后端 `skip_clarification` 命令；真正的权限、状态版本、合法动作、审计和状态迁移均由后端执行。

跳过命令成功后，页面使用返回的新 `state_version` 自动发送 `create_spec`。如果 PRD 生成失败，不回滚已经记录的跳过决定，页面刷新后可从 `SPECIFICATION` 重试生成。

### 0.4 Kanban 与审核入口

- ROOT 卡片：唯一的 PRD 审核入口；没有 PRD 时禁用打开。
- MILESTONE 卡片：展示规划层次和依赖，不允许拖拽修改后端状态。
- TASK 卡片：展示后端拆解结果；若存在 Agent Spec，卡片详情显示完整结构化内容。
- PRD 有未解决评论时，确认优先发布评论；没有评论时才批准并进入拆解。
- 所有通用按钮来自 `SessionState.legal_actions`。PRD 专属动作只显示在 ROOT 卡片详情，防止从侧栏绕过人工审核。

### 0.5 当前真实接口

| 前端能力 | 后端接口 |
| --- | --- |
| 健康检查 | `GET /healthz` |
| 创建与恢复 Session | `POST /sessions`、`GET /sessions/{id}/state` |
| 澄清、跳过、审核、拆解 | `POST /sessions/{id}/commands` |
| Spec / WorkItem / Agent Spec / 审计 | `GET /sessions/{id}/specs|work-items|agent-specs|events` |
| PRD 和有效行 | `GET /prd/{wi}`、`GET /prd/{wi}/commentable-lines` |
| 评论、回复、解决 | `POST /prd/{wi}/comments...` |
| 发布 PRD 审核 | `POST /prd/{wi}/reviews/publish`、`GET /tasks/{task_id}` |
| 兼容安全阶段流 | `POST /chat` |

公开 `CommandAction` 包含：`message`、`skip_clarification`、`create_spec`、`revise`、`approve`、`reject`、`rework`、`convert_to_work_item` 和 `restore_spec_version`。`publish_review` 是后端内部动作，不应由通用 Session 按钮直接伪造。

当前真实范围在生成子 WorkItem 和明确 Agent Spec 后结束；不执行子 Agent、代码、提交或部署，也不展示原始推理链。

---

## 1. 系统总体架构与数据流转拓扑

本系统是为 **CodeX 自主型多 Agent 协同群** 量身打造的企业级研发编排平台。核心思想是建立 **"用户意图输入 -> PM Agent 需求分析与 Gitea PRD 规格生成 -> 架构/开发/测试 Agent DAG 任务网络拓扑拆解 -> 并行编码与自动化提交 -> 审计追溯与云端持久化"** 的全生命周期自动化闭环。

```
                         ┌──────────────────────────────────────────────┐
                         │              用户交互与自然语言指令           │
                         └──────────────────────┬───────────────────────┘
                                                │ (WebSocket / SSE)
                                                ▼
                         ┌──────────────────────────────────────────────┐
                         │       Project Orchestrator Agent (PM)        │
                         │  - 分析自然语言指令并输出 Gitea PRD (Markdown) │
                         │  - 实例化 Root Epic (主工单)                 │
                         │  - 生成 DAG 多层任务拓扑节点及有向依赖关系       │
                         └──────────────────────┬───────────────────────┘
                                                │
                 ┌──────────────────────────────┼──────────────────────────────┐
                 ▼                              ▼                              ▼
      ┌────────────────────┐         ┌────────────────────┐         ┌────────────────────┐
      │ Architecture Agent │         │ Backend Dev Agent  │         │ Frontend Dev Agent │
      │ - 架构契约 & API设计│         │ - 数据模型与接口开发 │         │ - 交互组件与视图开发 │
      └──────────┬─────────┘         └──────────┬─────────┘         └──────────┬─────────┘
                 │                              │                              │
                 └──────────────────────────────┼──────────────────────────────┘
                                                │ (状态机流转 & 代码产出)
                                                ▼
                         ┌──────────────────────────────────────────────┐
                         │          QA & Security Audit Agents          │
                         │  - 静态代码分析 & 自动化单测门禁               │
                         │  - 审计日志记录 (ExecutionAuditLog)           │
                         └──────────────────────┬───────────────────────┘
                                                │
                                                ▼
                         ┌──────────────────────────────────────────────┐
                         │         云端资产存储 & 协同交付中心           │
                         │  - Gitea VCS / Git 提交记录与 Diff           │
                         │  - Google Drive 云端同步 (~/code/frontend)    │
                         └──────────────────────────────────────────────┘
```

---

## 2. 源码级工程目录结构与职责解耦

```text
├── src/
│   ├── components/                 # 业务视图与核心模态弹窗组件
│   │   ├── AgentChat.tsx           # 多 Agent 协同流式对话面板 (支持代码块、快捷动作、状态卡片)
│   │   ├── KanbanBoard.tsx         # 敏捷多泳道看板 (状态泳道/负责人泳道, 支持 Epic/Subtask 层次展示)
│   │   ├── TaskFlowDiagram.tsx     # DAG 任务依赖与执行拓扑网络 (SVG 动态贝塞尔曲线连接、执行状态高亮)
│   │   ├── CodeWorkspaceTab.tsx    # 代码工作区与 IDE 源码实时查看器 (多文件树导航、代码高亮、复制与导出)
│   │   ├── CommitHistoryTab.tsx    # Gitea / Git 提交流水与 Unified Diff 差异比对
│   │   ├── WorkItemDetailModal.tsx # 工单全量审计日志、时序流转记录与局部 Agent 对话抽屉
│   │   ├── PRDDocumentModal.tsx    # Gitea PRD Markdown 多点逐段批注工作台与版本演进引擎
│   │   ├── GoogleDriveModal.tsx    # Google Drive 源码一键导出与云端备份面板
│   │   ├── MiniPreviewWidget.tsx   # 右上角全局微缩看板挂件 (阻断监控、活跃任务数、快捷Tab切换)
│   │   └── SidebarNav.tsx          # 侧边主活动导航栏 & GitHub Light/Dark 主题切换器
│   ├── context/
│   │   └── ThemeContext.tsx        # GitHub 官方设计系统主题上下文 Provider (支持持久化与动态注入)
│   ├── services/
│   │   ├── googleDriveAuth.ts      # Google Drive OAuth / Firebase 鉴权与 Access Token 管理
│   │   └── googleDriveService.ts   # Google Drive v3 文件树遍历、目录递归创建与 Multipart 上传引擎
│   ├── utils/
│   │   ├── mockData.ts             # 初始工单、Agent 专家元数据、DAG 拓扑节点与示例 Git 提交
│   │   └── workflowEngine.ts       # 状态机合法性校验、流转历史生成、主任务自动拆解与 DAG 节点联动逻辑
│   ├── types.ts                    # 全局 TypeScript 强类型接口定义 (工单、Agent、DAG、PRD、审计日志)
│   ├── App.tsx                     # 应用根容器：集中维护全局状态、Tab 路由、模态窗显隐与事件分发
│   ├── main.tsx                    # React 19 应用启动入口，挂载 StrictMode 与 ThemeProvider
│   ├── index.css                   # 全局样式与 Tailwind CSS 4 声明
│   └── vite-env.d.ts               # Vite 客户端类型声明
├── firebase-applet-config.json     # Firebase / Google Drive OAuth 配置
├── package.json                    # 项目依赖管理与编译启动脚本
└── vite.config.ts                  # Vite 6 构建与插件配置
```

---

## 3. 核心数据模型与 TypeScript 类型体系设计 (`src/types.ts`)

`types.ts` 是整个系统的数据基石，定义了严格的业务约束与数据交互格式：

### 3.1 工单与状态机枚举 (`WorkItemStatus`, `WorkItemPriority`)
```typescript
// 严格的生命周期状态机枚举
export type WorkItemStatus = 'backlog' | 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked';
export type WorkItemPriority = 'P0' | 'P1' | 'P2' | 'P3';
```

### 3.2 Agent 专家元数据 (`AgentInfo`, `AgentRole`)
```typescript
export type AgentRole = 
  | 'project_agent'    // 负责 PRD、拆解、排期
  | 'architect_agent'  // 负责架构设计、接口契约
  | 'backend_agent'    // 负责服务端开发
  | 'frontend_agent'   // 负责前端界面与交互
  | 'qa_agent'         // 负责单元测试与集成测试
  | 'security_agent'   // 负责合规与漏洞扫描
  | 'user';            // 人类操作员

export interface AgentInfo {
  id: string;
  name: string;
  role: AgentRole;
  avatar: string;
  color: string;
  specialty: string;
}
```

### 3.3 工单核心结构 (`WorkItem`)
```typescript
export interface WorkItem {
  id: string;                         // 唯一标识，如 "WI-101"
  title: string;                      // 任务标题
  description: string;                // 任务详细描述
  type: 'epic' | 'story' | 'task' | 'bug' | 'subtask';
  status: WorkItemStatus;             // 当前流转状态
  priority: WorkItemPriority;         // 优先级
  assignee: AgentInfo;                // 当前指派的 Agent 专家
  reporter: AgentInfo;                // 提出/拆解该工单的 Agent
  createdAt: string;
  updatedAt: string;
  parentId?: string;                  // 父级 Epic ID (若为子任务)
  parentTitle?: string;
  subItemIds: string[];               // 派生子任务 ID 列表
  transitionHistory: TransitionHistory[]; // 完整状态变更审计轨迹
  commits: GitCommitRecord[];         // 关联的 Git 代码提交
  auditLogs: ExecutionAuditLog[];     // 详细执行日志 (包含 stdout, exitCode)
  itemChatMessages?: AgentChatMessage[]; // 局部针对该任务的讨论流
  prdVersions?: PRDVersion[];         // 若为主工单，绑定的 Gitea PRD 多版本快照
  activePrdVersion?: string;          // 当前生效 PRD 版本号 (如 "v1.1")
  tags: string[];                     // 标签
  estimatedHours: number;             // 预估工时
  loggedHours: number;                // 已消耗工时
  progress: number;                   // 进度百分比 (0-100)
  isRootEpic?: boolean;               // 是否为主 Epic
  stateNodeId?: string;               // 映射的 DAG 拓扑节点 ID
  bottleneckReason?: string;          // 阻断原因 (当 status === 'blocked')
}
```

### 3.4 Gitea PRD 与多点批注数据模型 (`PRDVersion`, `PRDComment`)
```typescript
export interface PRDComment {
  id: string;
  sectionId: string;        // 批注对应的章节 ID (如 "sec-1-0")
  sectionTitle: string;     // 批注章节标题 (如 "3.1 JWT Token TTL 规范")
  targetQuote: string;      // 引用的原文片段
  content: string;          // 用户/专家提出的修改意见
  author: AgentInfo;        // 批注作者
  createdAt: string;
  resolved: boolean;        // 是否已被 PM Agent 在新版本中解决
  resolutionNote?: string;  // PM Agent 解决说明
}

export interface PRDVersion {
  version: string;          // 版本号: "v1.0", "v1.1", "v1.2"
  title: string;
  createdAt: string;
  author: AgentInfo;
  giteaCommit: {
    repo: string;           // Gitea 仓库地址
    branch: string;         // 分支
    commitHash: string;     // Git Commit SHA-1
    filePath: string;       // 文件路径 (如 "docs/PRD.md")
  };
  summary: string;          // 该版本演进变更摘要
  content: string;          // 全量 Markdown 正文
  comments: PRDComment[];   // 绑定的批注列表
  isActive: boolean;        // 是否为当前生效版本
}
```

### 3.5 DAG 执行拓扑节点与有向边 (`FlowNode`, `FlowEdge`)
```typescript
export type FlowNodeStatus = 'idle' | 'running' | 'completed' | 'blocked' | 'failed' | 'waiting_approval';

export interface FlowNode {
  id: string;               // 节点唯一 ID (如 "node_auth_arch")
  title: string;
  type: 'start' | 'process' | 'condition' | 'agent_action' | 'review' | 'deploy' | 'end';
  status: FlowNodeStatus;
  assignedAgent: AgentInfo;
  relatedWorkItemId?: string; // 关联的 WorkItem ID
  inputs: string[];         // 前置依赖节点 ID 数组
  outputs: string[];        // 后置流向节点 ID 数组
  bottleneck: boolean;      // 是否为瓶颈节点
  bottleneckMsg?: string;
  duration: string;         // 耗时 (如 "14m")
  latencyMs?: number;       // 毫秒级延迟
  x: number;                // 拓扑图水平绘制坐标
  y: number;                // 拓扑图垂直绘制坐标
  description: string;
  dynamicSpawned?: boolean; // 是否由子任务动态生成
}

export interface FlowEdge {
  id: string;
  source: string;           // 起始节点 ID
  target: string;           // 目标节点 ID
  label?: string;
  active?: boolean;         // 边是否处于激活流转中
  isBottleneck?: boolean;
}
```

---

## 4. 核心功能模块源码级深度解析与逻辑关系

### 4.1 根应用容器与全局状态中心 (`src/App.tsx`)
- **文件职责**:
  1. 充当整个应用的 **单一直理源（Single Source of Truth）**，集中管理 `workItems`、`flowNodes`、`flowEdges`、`chatMessages` 等核心数据集。
  2. 控制 4 大主视图 Tab（`kanban` 看板、`flow` 拓扑流、`code` 代码工作区、`commits` Git 提交追溯）的路由切换。
  3. 挂载 3 大核心全局弹窗：工单详情审计模态窗 (`WorkItemDetailModal`)、Gitea PRD 批注工作台 (`PRDDocumentModal`) 与 Google Drive 导出模态窗 (`GoogleDriveModal`)。
- **关键状态流与处理函数**:
  - `handleStatusChange(itemId, newStatus)`: 接收来自看板拖拽/点击或详情页的状态跳转请求，调用 `workflowEngine.validateTransition()` 进行状态机合法性判断，并通过 `generateSubItemsForEpic()` 实现主工单自动拆解子任务与拓扑节点动态挂载。
  - `handleUpdateWorkItemPRD(workItemId, updatedVersions, activeVersion)`: 接收来自 PRD 批注工作台演进后的新版本数据，同步更新主工单状态、生成审计日志，并在 Agent 聊天流中自动广播 PRD 版本演进通知。

---

### 4.2 状态机约束引擎与自动化任务分解器 (`src/utils/workflowEngine.ts`)
- **文件职责**:
  封装与 UI 无关的纯业务逻辑与状态机算法，为后端 Agent 编排引擎提供前端对等实现参考。
- **核心算法解析**:
  1. **状态转移合法性校验 (`validateTransition`)**:
     定义严格的合法转移路径矩阵：
     - `backlog` $\to$ `todo` / `blocked`
     - `todo` $\to$ `in_progress` / `blocked`
     - `in_progress` $\to$ `in_review` / `blocked`
     - `in_review` $\to$ `done` / `in_progress` (被打回) / `blocked`
     - `blocked` $\to$ 恢复前置状态
  2. **主任务自动分解算法 (`generateSubItemsForEpic`)**:
     当主 Epic 被拖入 `in_progress` 时触发：
     - 自动派生 2 个子任务：`[数据层与 API 接口实现]`（指派给 `backend_agent`）和 `[前端交互组件与状态管理]`（指派给 `frontend_agent`）。
     - 自动生成子任务对应的 DAG 拓扑节点（`FlowNode`）及前后依赖有向边（`FlowEdge`），并将其计算插入拓扑网络图。
     - 为子任务预先填充初始化的 Git Commit 记录、构建审计日志（`ExecutionAuditLog`）和 Agent 初始讨论流。
  3. **DAG 拓扑双向联动 (`syncWorkItemStatusToFlowNode`)**:
     确保工单状态变更（如进入 `done` 或 `blocked`）时，拓扑图中的对应节点实时更新样式、高亮脉冲环与阻断告警标记。

---

### 4.3 敏捷泳道看板与多维过滤视图 (`src/components/KanbanBoard.tsx`)
- **文件职责**:
  提供敏捷研发管理的高效看板视图，直观展示各研发阶段的工单分布与瓶颈。
- **功能特性与实现细节**:
  - **双重视角分组**:
    - **按生命周期状态 (Status Columns)**: `Backlog`、`To Do`、`In Progress`、`In Review`、`Done`。
    - **按 Agent 负责人 (Assignee Columns)**: 展现 PM、架构师、前端、后端、QA、安全 Agent 各自当前负载与在办任务。
  - **Epic 与 Subtask 视觉分层**:
    - 主工单高亮显示 `Epic` 徽章、当前生效的 `PRD v1.x` 药丸标签、进度条与子任务计数（如 `2/4 Subtasks`）。
    - 点击主工单上的 `PRD` 标签直接唤起 Gitea PRD 批注工作台；点击卡片主体唤起工单审计详情。
  - **多维筛选引擎**: 组合式计算过滤条件，支持同时过滤优先级（P0/P1/P2/P3）、执行角色、搜索关键词以及 `只看阻断任务 (Bottlenecks)`。

---

### 4.4 DAG 任务依赖与执行拓扑网络 (`src/components/TaskFlowDiagram.tsx`)
- **文件职责**:
  可视化展示由 PM Agent 拆解的多任务依赖网络图（Directed Acyclic Graph），呈现任务间的并行与阻塞拓扑。
- **图形渲染与交互实现**:
  - **分层拓扑坐标映射**:
    根据节点的 `layer` 属性和依赖关系计算二维网格坐标 $(x, y)$，保证图表自左向右分层展开（Layer 1 架构规划 $\to$ Layer 2 核心开发 $\to$ Layer 3 测试验证 $\to$ Layer 4 部署发布）。
  - **动态 SVG 贝塞尔曲线连接线**:
    计算起点与终点锚点坐标，使用 `M x1 y1 C cx1 cy1, cx2 cy2, x2 y2` 生成平滑三次贝塞尔曲线；对于处于进行中状态的边赋予 `stroke-dasharray` 跑马灯流动动画；对阻断依赖边赋予琥珀色高亮。
  - **节点交互与状态感知**:
    节点内嵌 Agent 头像、耗时胶囊与状态徽章，悬浮时高亮关联依赖路径，点击后联动打开关联工单的全量审计详情。

---

### 4.5 Gitea PRD 规格文档多点逐段批注与演进系统 (`src/components/PRDDocumentModal.tsx`)
- **文件职责**:
  实现产品需求文档（PRD）从初始编写、多方评审、章节逐段批注到由 PM Agent 自主重构演进的全流程闭环。
- **核心业务逻辑与交互创新**:
  1. **Gitea 仓库映射区**:
     顶部常驻展示当前 PRD 绑定的 Gitea 仓库 URL（`gitea.corp.ai/enterprise/...`）、分支、Commit Hash 及文档路径（`docs/PRD.md`），并提供一键跳转链接。
  2. **多点逐段批注暂存机制 (Multi-Point Annotation Staging)**:
     - 用户在阅读 Markdown 渲染的 PRD 正文时，各章节标题旁均提供 **「在此章节写批注」** 快捷按钮。
     - 触发后，右侧批注面板激活，自动锁定目标章节（如 `3.1 JWT Token TTL 规范`）。
     - 支持 **多点批量暂存**：用户可在一次评审会话中，对 3~5 处不同的技术段落同时添加修改意见，并使用快捷模板（如 *[安全强化]*、*[性能调优]*、*[超时缩短]*）快速填入。
  3. **PM Agent 自动化重构与版本演进 (`handleEvolvePRD`)**:
     - 点击 **「批量提交批注并由 PM Agent 演进新版」** 时，系统将所有待处理批注打包下发。
     - PM Agent 解析批注诉求，自动重构 Markdown 文本、将已采纳批注标记为 `resolved`、生成新的 Commit Hash 并递增版本号（如 `v1.0` $\to$ `v1.1`）。
     - 自动向全局状态中心分发更新，主工单与 Agent 聊天流实时同步。
  4. **版本快照对比与回滚机制**:
     左侧提供完整版本历史侧边栏，清晰列出各版本的创建时间、提交者、变更摘要及关联批注数，支持随时查看历史版本并一键执行 **「回滚至此版本」**。

---

### 4.6 多 Agent 协同流式对话面板 (`src/components/AgentChat.tsx`)
- **文件职责**:
  提供用户与各个自主 Agent 对话、下达自然语言指令及查看 Agent 思考链（Thought Chain）的操作台。
- **关键设计**:
  - **多角色消息流**: 区分 PM、架构师、开发、QA 及人类用户的消息气泡与专属色彩标识。
  - **工单上下文绑定**: 顶部支持快速切换或清除当前对话绑定的 `Context WorkItem`，使 Agent 回答聚焦于特定任务。
  - **Agent 快捷动作卡片 (Quick Actions)**:
    支持渲染一键触发的操作按钮（如：`一键生成网关模块子任务`、`触发安全漏洞扫描`、`部署至预发环境`），点击后直接调用 `workflowEngine` 相应方法并在看板上产生实体变更。

---

### 4.7 工单全生命周期审计与历史详情抽屉 (`src/components/WorkItemDetailModal.tsx`)
- **文件职责**:
  作为单个工单的深度审计看板，提供四维一体的详情下钻：
  1. **基础信息与工时进度**: 优先级、指派人、关联 Epic、工时占比与进度滑块。
  2. **时序流转历史 (Transition Timeline)**: 倒序呈现每一次状态跃迁的时间、操作者、变更原因及在上一状态停留时长。
  3. **代码提交记录 (Git Commits)**: 展示关联的 Commit Hash 与变更文件列表。
  4. **底层执行日志 (Audit Logs)**: 展示 Agent 执行工具调用时的标准输出（stdout）、输入参数、耗时及退出码（exitCode）。

---

### 4.8 代码工作区与 IDE 源码实时查看器 (`src/components/CodeWorkspaceTab.tsx`)
- **文件职责**:
  展示 Agent 自动化生成的实际代码工程结构，提供类似 VS Code 的轻量级代码阅读与导出体验。
- **功能特性**:
  - 左侧文件树按目录（`src/api/`、`src/middleware/`、`src/services/`）组织，支持快速切换活动文件。
  - 右侧提供语法高亮、一键全选复制、文件下载功能。
  - 工具栏常驻 **「保存代码至 Google Drive」** 按钮，直连 Google Drive 云端同步模态窗。

---

### 4.9 Git / Gitea 自动化提交追溯流 (`src/components/CommitHistoryTab.tsx`)
- **文件职责**:
  汇聚整个研发流水线中所有 Agent 产生的 Git 提交记录。
- **功能特性**:
  - 展示提交哈希、提交信息、提交 Agent、分支名称与时间戳。
  - 支持展开查看真实的 **Unified Diff 代码行比对**（绿色新增行 `+` 与红色删除行 `-`）。

---

### 4.10 Google Drive 全量源码云端同步引擎 (`src/services/` & `src/components/GoogleDriveModal.tsx`)
- **文件职责**:
  实现将本应用前端项目全量源代码（包含组件、样式、工具类及配置文件）无缝备份至用户 Google Drive `~/code/frontend` 目录。
- **模块实现细节**:
  - `googleDriveAuth.ts`:
    - 基于 Firebase Auth 与 Google Drive 权限范围（`https://www.googleapis.com/auth/drive`、`.../drive.file`）进行 OAuth 2.0 弹窗授权。
    - 实现 Access Token 内存级安全缓存与登录用户状态监听。
  - `googleDriveService.ts`:
    - `getAppCodebaseFiles()`: 利用 Vite 提供的 `import.meta.glob(..., { query: '?raw', eager: true })` 特性，在运行时动态抓取前端所有核心源文件内容并自动补充 `README.md`。
    - `findOrCreateFolder()`: 递归定位或创建 Google Drive 文件夹。
    - `ensureFolderPath()`: 确保云端目录 `~/code/frontend` 层级完备。
    - `uploadFileToDrive()`: 采用 Google Drive v3 Multipart Upload 协议，精准匹配 MIME 类型（`text/typescript`、`application/json` 等）执行上传与覆盖更新。
  - `GoogleDriveModal.tsx`:
    - 提供 **Google 账号登录授权 -> 确认操作步骤 -> 实时上传进度条 -> 上传文件明细清单 -> 完成后一键在 Drive 中打开文件夹** 的完整交互闭环。

---

### 4.11 GitHub Light / Dark 双主题引擎 (`src/context/ThemeContext.tsx` & `src/index.css`)
- **文件职责**:
  提供完全遵循 GitHub 官方规范的设计系统主题引擎。
- **配色规范与实现**:
  - `ThemeContext.tsx`: 管理 `theme` 状态（`'light' | 'dark'`），并在 `<html>` 根标签上动态切换 `.dark` / `.light` CSS 类，同时向 `localStorage` 进行持久化同步。
  - `index.css`:
    - **GitHub Light 浅色模式规范**:
      - 主画布背景: `#ffffff`
      - 面板/表头/侧边栏: `#f6f8fa`
      - 边框与分割线: `#d0d7de` 与 `#e1e4e8`
      - 主文字与次级文字: `#1f2328` 与 `#656d76`
      - 主操作按钮: GitHub 标志性绿色 `#1f883d`（Hover: `#1a7f37`）
      - 品牌高亮色: `#0969da`
    - **GitHub Dark 深色模式规范**:
      - 主画布背景: `#0d1117`
      - 次级面板: `#161b22`
      - 边框: `#30363d`
      - 文字: `#c9d1d9` 与 `#8b949e`
  - 左下角常驻主题切换按钮（`#btn-theme-toggle`）与状态栏快捷入口，方便用户随时切换。

---

## 5. 组件间协作与逻辑关系调用图

以下图表清晰展现了主要组件之间的状态分发与事件回调链条：

```
                                  ┌────────────────────────┐
                                  │      ThemeContext      │
                                  │ (GitHub Light/Dark 状态)│
                                  └───────────┬────────────┘
                                              │ (注入主题)
                                              ▼
                                  ┌────────────────────────┐
                                  │        App.tsx         │
                                  │ (全局数据与状态中心)     │
                                  └───┬────────────────┬───┘
         ┌────────────────────────────┼────────────────┴───────────────────────────┐
         │ (workItems / flowNodes)    │ (activeTab 切换)                           │ (isModalOpen 状态)
         ▼                            ▼                                            ▼
┌──────────────────┐        ┌──────────────────┐                         ┌──────────────────┐
│ KanbanBoard.tsx  │        │ TaskFlowDiagram  │                         │ PRDDocumentModal │
│ - 状态/负责人泳道 │        │ - DAG 拓扑渲染   │                         │ - Gitea PRD 批注 │
│ - 过滤与拖拽流转 │        │ - 瓶颈节点高亮   │                         │ - PM Agent 演进  │
└────────┬─────────┘        └────────┬─────────┘                         └────────┬─────────┘
         │                           │                                            │
         │ (点击工单卡片)             │ (点击拓扑节点)                             │ (提交批注演进)
         └─────────────┬─────────────┘                                            │
                       ▼                                                          │
          ┌─────────────────────────┐                                             │
          │ WorkItemDetailModal.tsx │                                             │
          │ - 状态流转时序审计轨迹  │                                             │
          │ - Git 提交与底层日志    │                                             │
          └────────────┬────────────┘                                             │
                       │                                                          │
                       ▼ (状态变更 / 批注提交)                                     ▼
          ┌─────────────────────────────────────────────────────────────────────────────┐
          │                         src/utils/workflowEngine.ts                         │
          │  - validateTransition() 状态机校验                                          │
          │  - generateSubItemsForEpic() 主任务自动分解与 DAG 节点联动                  │
          │  - createTransitionRecord() 审计记录生成                                    │
          └─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. CodeX 后端标准接口契约规范

为了将本前端系统完全对接至真实 CodeX 后端微服务与 Agent 运行时，前端已预留并规范了以下标准 RESTful / SSE / WebSocket 接口：

```text
Base URL: https://api.codex.internal/v1
认证方式: Bearer Token (Authorization: Bearer <JWT>)
数据交互格式: Content-Type: application/json
```

---

### 6.1 工单与生命周期流转接口 (WorkItem APIs)

#### 1. 获取工单列表
- **Endpoint**: `GET /v1/work-items`
- **Query Parameters**:
  - `projectId`: string (项目 ID)
  - `status`: string (可选, 过滤状态: `backlog` | `todo` | `in_progress` | `in_review` | `done` | `blocked`)
  - `assigneeRole`: string (可选, 过滤 Agent 角色)
- **Response**:
```json
{
  "code": 0,
  "data": [
    {
      "id": "WI-101",
      "title": "设计统一微服务网关与 JWT 鉴权协议",
      "type": "epic",
      "status": "in_progress",
      "priority": "P0",
      "assignee": {
        "id": "agent-pm-01",
        "name": "Project Orchestrator Agent",
        "role": "project_agent",
        "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=100&auto=format&fit=crop&q=80",
        "color": "#a855f7",
        "specialty": "需求分析、PRD编写与多任务分解"
      },
      "description": "设计微服务网关的路由转发规则与 JWT 鉴权中间件...",
      "subItemIds": ["WI-201", "WI-202"],
      "estimatedHours": 32,
      "loggedHours": 14,
      "progress": 45,
      "activePrdVersion": "v1.1",
      "tags": ["Architecture", "Gateway", "Security"],
      "createdAt": "2026-09-01T10:00:00Z",
      "updatedAt": "2026-09-01T14:30:00Z"
    }
  ]
}
```

#### 2. 状态机跳转与状态变更 (含审计日志自动生成)
- **Endpoint**: `POST /v1/work-items/{itemId}/transitions`
- **Request Body**:
```json
{
  "fromStatus": "todo",
  "toStatus": "in_progress",
  "actor": {
    "id": "agent-backend-01",
    "name": "Backend Dev Agent",
    "role": "backend_agent"
  },
  "reason": "已完成网关鉴权中间件的接口设计，正式进入编码开发阶段",
  "notes": "自动派生数据层与前端组件两个子任务并接入 DAG 拓扑",
  "metadata": {
    "branch": "feature/WI-101-jwt-auth",
    "commitHash": "7f8b92c"
  }
}
```
- **Response**:
```json
{
  "code": 0,
  "data": {
    "success": true,
    "currentStatus": "in_progress",
    "transitionId": "th-1725200000000-abcd",
    "timestamp": "2026-09-01 14:32:00",
    "spawnedSubItems": ["WI-201", "WI-202"]
  }
}
```

---

### 6.2 Gitea PRD 规格文档与批注演进接口 (PRD Management APIs)

#### 1. 获取主工单关联的 Gitea PRD 全量版本与批注历史
- **Endpoint**: `GET /v1/work-items/{itemId}/prd`
- **Response**:
```json
{
  "code": 0,
  "data": {
    "activeVersion": "v1.1",
    "versions": [
      {
        "version": "v1.1",
        "title": "Core Auth PRD v1.1",
        "createdAt": "2026-09-01 12:00",
        "author": { "name": "Project Orchestrator Agent", "role": "project_agent" },
        "giteaCommit": {
          "repo": "gitea.corp.ai/enterprise/core-auth",
          "branch": "main",
          "commitHash": "e8f192b",
          "filePath": "docs/PRD.md"
        },
        "summary": "演进版本：采纳架构组 2 处批注，缩短 Access Token TTL 并增加 TOTP 双因子流程。",
        "content": "# Core Auth 需求规格说明书\n\n## 1.0 项目背景...",
        "comments": [
          {
            "id": "cmt-101",
            "sectionId": "sec-3-1",
            "sectionTitle": "3.1 JWT Token TTL 规范",
            "targetQuote": "Access Token 有效期默认配置为 2 小时",
            "content": "建议将 Access Token 过期时间从 2h 缩短为 15m，并引入 Refresh Token 轮转机制。",
            "author": { "name": "Tech Lead (Human)", "role": "user" },
            "createdAt": "2026-09-01 11:45",
            "resolved": true,
            "resolutionNote": "PM Agent 已修订 3.1 节参数为 15m 并补充双 Token 交互时序图"
          }
        ],
        "isActive": true
      }
    ]
  }
}
```

#### 2. 批量提交批注并触发 PM Agent 演进新版 PRD
- **Endpoint**: `POST /v1/work-items/{itemId}/prd/evolve`
- **Request Body**:
```json
{
  "baseVersion": "v1.1",
  "annotations": [
    {
      "sectionId": "sec-3-2",
      "sectionTitle": "3.2 2FA 双因子认证",
      "targetQuote": "仅支持基于邮件验证码的二次认证",
      "commentText": "补充支持 WebAuthn / 硬件安全密钥 (FIDO2) 认证链路，满足金融级合规。"
    },
    {
      "sectionId": "sec-4-1",
      "sectionTitle": "4.1 速率限制策略",
      "targetQuote": "登录端点限流为 100 次/分钟",
      "commentText": "登录接口限流由 100次/分 收紧至 10次/分，防止暴力破解。"
    }
  ]
}
```
- **Response**:
```json
{
  "code": 0,
  "data": {
    "newVersion": "v1.2",
    "giteaCommitHash": "c4d710e",
    "summary": "由 PM Agent 解析 2 条批注并完成自动重构：已补充 FIDO2 规格并调整接口限流阈值。",
    "updatedContent": "# Core Auth 需求规格说明书 v1.2...",
    "resolvedCommentIds": ["cmt-102", "cmt-103"],
    "broadcastMessage": "PM Agent 已完成 PRD 演进至 v1.2，下游 Backend/QA Agent 依赖已自动同步。"
  }
}
```

#### 3. PRD 版本回滚
- **Endpoint**: `POST /v1/work-items/{itemId}/prd/rollback`
- **Request Body**:
```json
{
  "targetVersion": "v1.0",
  "reason": "回滚至架构基线版本以重新评估多区域容灾方案"
}
```

---

### 6.3 Agent 对话流与流式通信 (SSE / WebSocket)

#### 1. 发送 Agent 对话指令 (流式返回)
- **Endpoint**: `POST /v1/agents/chat/stream` (Server-Sent Events)
- **Request Body**:
```json
{
  "sessionId": "sess-eng-2026",
  "message": "请帮我将当前网关模块拆解出 限流中间件 和 Prometheus指标采集 两个子工单",
  "contextWorkItemId": "WI-101"
}
```
- **SSE Event Stream 格式**:
```text
event: thought_chain
data: {"step": "正在检索 WI-101 关联的 Gitea PRD 规格文档与架构设计..."}

event: thought_chain
data: {"step": "计算子任务依赖关系：限流中间件 -> Prometheus指标采集..."}

event: token
data: {"chunk": "已为您完成子工单拆解方案：\n1. **WI-203 Token Bucket 限流中间件**\n2. **WI-204 Prometheus 指标采集与 Grafana 仪表盘**"}

event: tool_call
data: {"tool": "decompose_work_item", "params": {"parentId": "WI-101", "subtasks": [...]}}

event: complete
data: {
  "messageId": "msg-992",
  "status": "done",
  "actionType": "spawn_subtasks",
  "quickActions": [
    { "actionKey": "approve_decomposition", "label": "确认并生成子任务拓扑" }
  ]
}
```

---

### 6.4 DAG 拓扑网络与依赖计算接口

#### 1. 获取全景 DAG 执行拓扑图
- **Endpoint**: `GET /v1/topology/dag`
- **Response**:
```json
{
  "code": 0,
  "data": {
    "nodes": [
      {
        "id": "node_auth_arch",
        "title": "网关架构与 API 契约设计",
        "type": "process",
        "status": "completed",
        "assignedAgent": { "id": "agent-arch-01", "name": "System Architect Agent", "role": "architect_agent" },
        "relatedWorkItemId": "WI-101",
        "inputs": [],
        "outputs": ["node_jwt_dev"],
        "bottleneck": false,
        "duration": "18h",
        "x": 60,
        "y": 140,
        "description": "设计微服务网关的核心架构与 OpenAPI 契约"
      },
      {
        "id": "node_jwt_dev",
        "title": "JWT 中间件核心开发",
        "type": "agent_action",
        "status": "running",
        "assignedAgent": { "id": "agent-backend-01", "name": "Backend Dev Agent", "role": "backend_agent" },
        "relatedWorkItemId": "WI-102",
        "inputs": ["node_auth_arch"],
        "outputs": ["node_qa_test"],
        "bottleneck": false,
        "duration": "14h",
        "x": 260,
        "y": 140,
        "description": "实现 JWT 校验、公私钥签名与 Redis 黑名单机制"
      }
    ],
    "edges": [
      { "id": "e_arch_jwt", "source": "node_auth_arch", "target": "node_jwt_dev", "active": true }
    ]
  }
}
```

---

### 6.5 代码版本控制与提交追溯接口 (VCS / Gitea)

#### 1. 获取代码仓库提交记录与 Diff
- **Endpoint**: `GET /v1/vcs/commits?repo=enterprise/core-auth&limit=20`
- **Response**:
```json
{
  "code": 0,
  "data": [
    {
      "id": "c-101-1",
      "hash": "7f8b92c4a1e9b2c3d4e5f6a7b8c9d0e1f2a3b4c5",
      "shortHash": "7f8b92c",
      "message": "feat(gateway): implement token bucket rate limiter & prometheus metrics",
      "author": { "id": "agent-backend-01", "name": "Backend Dev Agent", "role": "backend_agent" },
      "timestamp": "2026-09-01 14:10:00",
      "filesChanged": 3,
      "insertions": 142,
      "deletions": 12,
      "files": [
        {
          "name": "src/middleware/rateLimiter.ts",
          "status": "added",
          "diffSnippet": "@@ -0,0 +1,45 @@\n+import { Request, Response, NextFunction } from 'express';\n+export const rateLimiter = (req: Request, res: Response, next: NextFunction) => { ... }"
        }
      ]
    }
  ]
}
```

---

## 7. 后端对接与工程落地建议 (Best Practices)

1. **环境配置与注入 (`.env`)**:
   在将前端部署至生产环境对接真实 CodeX 后端时，配置以下环境变量即可：
   ```env
   # CodeX 后端 API 网关服务地址
   VITE_API_BASE_URL=https://api.codex.internal/v1

   # Gitea 私有代码托管平台地址
   VITE_GITEA_BASE_URL=https://gitea.corp.ai

   # SSE / WebSocket 流式服务地址
   VITE_WS_STREAM_URL=wss://api.codex.internal/v1/stream
   ```

2. **状态机防并发冲突 (HTTP 409 Conflict)**:
   - 所有的工单状态流转请求均必须在 Request Payload 中携带 `fromStatus`。
   - 若后端发现该工单的状态已被其他 Agent 或操作员修改（状态漂移），应直接返回 `HTTP 409 Conflict`，前端将捕获该错误并自动触发数据全量刷新与用户通知。

3. **Gitea Webhook 实时通知机制**:
   建议在 Gitea 服务端配置 Webhook，当 Agent 提交 Commit 或 PRD 文档发生变更时，向 CodeX 后端发送推送事件，后端通过 WebSocket 广播给前端，实现多人/多 Agent 协同界面的零延迟自动刷新。

4. **安全与敏感写操作防误触**:
   对于 PRD 版本回滚、工单批量删除、全量代码云端覆盖导出等敏感写操作，前端已内置多重拦截与二次确认模态窗，后端亦应配置对应的人类操作员审计日志与权限校验。

---
*文档编写完成，符合企业级工程化交付标准，可直接提交至 CodeX 后端架构组与研发团队执行对接。*
