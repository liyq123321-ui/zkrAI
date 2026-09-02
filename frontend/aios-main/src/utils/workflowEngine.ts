import { AGENTS } from './mockData';
import { FlowEdge, FlowNode, WorkItem, WorkItemStatus, TransitionHistory, GitCommitRecord } from '../types';

export function calculateDuration(startTimeStr?: string): string {
  if (!startTimeStr) return '1m';
  return 'Just now';
}

export function createTransitionRecord(
  fromStatus: WorkItemStatus,
  toStatus: WorkItemStatus,
  actor = AGENTS.project_agent,
  reason = '状态流转操作'
): TransitionHistory {
  const now = new Date();
  const timeStr = now.toISOString().replace('T', ' ').slice(0, 19);
  return {
    id: `th-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    timestamp: timeStr,
    fromStatus,
    toStatus,
    actor,
    reason,
    durationInPrevState: '24m',
  };
}

export function generateSubItemsForEpic(parent: WorkItem): { subItems: WorkItem[]; newNodes: FlowNode[]; newEdges: FlowEdge[] } {
  const timestamp = new Date().toISOString().replace('T', ' ').slice(0, 16);
  const baseNum = Math.floor(Math.random() * 800) + 200;

  const sub1: WorkItem = {
    id: `WI-${baseNum + 1}`,
    title: `${parent.title} - 数据层与 API 接口实现`,
    description: `由主任务 [${parent.id}] 分解产生：负责核心数据库表设计、CRUD 接口与后端业务校验逻辑。`,
    type: 'task',
    status: 'in_progress',
    priority: 'P0',
    assignee: AGENTS.backend_agent,
    reporter: AGENTS.project_agent,
    createdAt: timestamp,
    updatedAt: timestamp,
    parentId: parent.id,
    parentTitle: parent.title,
    subItemIds: [],
    stateNodeId: `node_gen_${baseNum}_be`,
    estimatedHours: 14,
    loggedHours: 3,
    progress: 35,
    tags: ['Backend', 'API', 'Generated'],
    transitionHistory: [
      {
        id: `th-${Date.now()}-1`,
        timestamp,
        fromStatus: 'backlog',
        toStatus: 'in_progress',
        actor: AGENTS.project_agent,
        reason: `主任务 ${parent.id} 移入 In Progress，自动分解派发。`,
      },
    ],
    commits: [
      {
        id: `c-${baseNum}-1`,
        hash: `${Math.random().toString(16).slice(2, 10)}${Math.random().toString(16).slice(2, 10)}`,
        shortHash: Math.random().toString(16).slice(2, 9),
        message: `feat(core): initial scaffolding for ${parent.title.slice(0, 20)}`,
        author: AGENTS.backend_agent,
        timestamp,
        filesChanged: 3,
        insertions: 110,
        deletions: 4,
        files: [
          {
            name: `src/api/${parent.id.toLowerCase()}/router.ts`,
            status: 'added',
            diffSnippet: `+ import { Router } from 'express';\n+ export const router = Router();\n+ router.get('/status', (req, res) => res.json({ ready: true }));`,
          },
        ],
      },
    ],
    auditLogs: [
      {
        id: `log-${baseNum + 1}-1`,
        timestamp,
        level: 'INFO',
        stepName: '子任务分解与数据库模型初始化 (Model Scaffold)',
        phase: 'planning',
        executor: AGENTS.backend_agent,
        summary: `从主工单 ${parent.id} 继承上下文并构建 API 路由。`,
        durationMs: 195,
        details: {
          inputParams: { parentId: parent.id, taskType: 'Backend Core' },
          stdout: `[INFO] Initialized backend module for ${parent.title}\n[SUCCESS] Router registered.`,
          exitCode: 0,
        },
      },
    ],
    itemChatMessages: [
      {
        id: `ic-${baseNum + 1}-1`,
        sender: AGENTS.backend_agent,
        content: `已承接 **[WI-${baseNum + 1}] 数据层与 API 接口实现**。当前处于 In Progress，可在下方随时输入指令调整实现细节。`,
        timestamp: 'Just now',
      },
    ],
  };

  const sub2: WorkItem = {
    id: `WI-${baseNum + 2}`,
    title: `${parent.title} - 客户端交互组件与状态管理`,
    description: `由主任务 [${parent.id}] 分解产生：负责交互组件、状态持久化与响应式适配。`,
    type: 'task',
    status: 'todo',
    priority: 'P1',
    assignee: AGENTS.frontend_agent,
    reporter: AGENTS.project_agent,
    createdAt: timestamp,
    updatedAt: timestamp,
    parentId: parent.id,
    parentTitle: parent.title,
    subItemIds: [],
    stateNodeId: `node_gen_${baseNum}_fe`,
    estimatedHours: 10,
    loggedHours: 0,
    progress: 0,
    tags: ['Frontend', 'UI', 'Generated'],
    transitionHistory: [
      {
        id: `th-${Date.now()}-2`,
        timestamp,
        fromStatus: 'backlog',
        toStatus: 'todo',
        actor: AGENTS.project_agent,
        reason: `主任务 ${parent.id} 移入 In Progress，自动分解派发。`,
      },
    ],
    commits: [],
    auditLogs: [
      {
        id: `log-${baseNum + 2}-1`,
        timestamp,
        level: 'INFO',
        stepName: 'UI 组件原型与状态树构建排队 (Frontend Standby)',
        phase: 'planning',
        executor: AGENTS.frontend_agent,
        summary: `准备 React 组件树与响应式布局。`,
        durationMs: 120,
        details: {
          inputParams: { theme: 'AI Studio Neutral' },
          stdout: `[INFO] Component tree definition queued.`,
          exitCode: 0,
        },
      },
    ],
    itemChatMessages: [
      {
        id: `ic-${baseNum + 2}-1`,
        sender: AGENTS.frontend_agent,
        content: `我是 **Frontend UI Agent**，该工单已就绪，可随时指令开始渲染组件。`,
        timestamp: 'Just now',
      },
    ],
  };

  const sub3: WorkItem = {
    id: `WI-${baseNum + 3}`,
    title: `${parent.title} - 端到端测试与质量门禁审计`,
    description: `由主任务 [${parent.id}] 分解产生：编写自动化测试套件与安全边界验证。`,
    type: 'task',
    status: 'todo',
    priority: 'P1',
    assignee: AGENTS.qa_agent,
    reporter: AGENTS.project_agent,
    createdAt: timestamp,
    updatedAt: timestamp,
    parentId: parent.id,
    parentTitle: parent.title,
    subItemIds: [],
    stateNodeId: `node_gen_${baseNum}_qa`,
    estimatedHours: 8,
    loggedHours: 0,
    progress: 0,
    tags: ['QA', 'E2E', 'Generated'],
    transitionHistory: [
      {
        id: `th-${Date.now()}-3`,
        timestamp,
        fromStatus: 'backlog',
        toStatus: 'todo',
        actor: AGENTS.project_agent,
        reason: `主任务 ${parent.id} 移入 In Progress，自动分解派发。`,
      },
    ],
    commits: [],
    auditLogs: [
      {
        id: `log-${baseNum + 3}-1`,
        timestamp,
        level: 'INFO',
        stepName: '测试门禁与断言矩阵准备 (Gate Matrix)',
        phase: 'testing',
        executor: AGENTS.qa_agent,
        summary: '等待前后端依赖就绪后触发自动化测试套件。',
        durationMs: 90,
        details: {
          inputParams: { coverageGate: '85%' },
          stdout: '[INFO] Awaiting upstream PR completion.',
          exitCode: 0,
        },
      },
    ],
    itemChatMessages: [
      {
        id: `ic-${baseNum + 3}-1`,
        sender: AGENTS.qa_agent,
        content: `我是 **QA Agent**，测试流水线已就绪。`,
        timestamp: 'Just now',
      },
    ],
  };

  // Generate corresponding Flow Nodes for the State Machine Graph
  const newNodes: FlowNode[] = [
    {
      id: sub1.stateNodeId!,
      title: `${sub1.title.slice(0, 24)}... (${sub1.id})`,
      type: 'agent_action',
      status: 'running',
      assignedAgent: AGENTS.backend_agent,
      relatedWorkItemId: sub1.id,
      inputs: [parent.stateNodeId || 'node_epic_auth'],
      outputs: [sub3.stateNodeId!],
      bottleneck: false,
      duration: 'Active',
      latencyMs: 310,
      x: 640 + (Math.random() * 80 - 40),
      y: 480 + (Math.random() * 40),
      description: sub1.description,
      dynamicSpawned: true,
    },
    {
      id: sub2.stateNodeId!,
      title: `${sub2.title.slice(0, 24)}... (${sub2.id})`,
      type: 'agent_action',
      status: 'idle',
      assignedAgent: AGENTS.frontend_agent,
      relatedWorkItemId: sub2.id,
      inputs: [parent.stateNodeId || 'node_epic_auth'],
      outputs: [sub3.stateNodeId!],
      bottleneck: false,
      duration: 'Queued',
      latencyMs: 0,
      x: 640 + (Math.random() * 80 - 40),
      y: 600 + (Math.random() * 40),
      description: sub2.description,
      dynamicSpawned: true,
    },
    {
      id: sub3.stateNodeId!,
      title: `${sub3.title.slice(0, 24)}... (${sub3.id})`,
      type: 'condition',
      status: 'idle',
      assignedAgent: AGENTS.qa_agent,
      relatedWorkItemId: sub3.id,
      inputs: [sub1.stateNodeId!, sub2.stateNodeId!],
      outputs: ['node_integration'],
      bottleneck: false,
      duration: 'Pending Subtasks',
      latencyMs: 0,
      x: 960 + (Math.random() * 60 - 30),
      y: 540 + (Math.random() * 40),
      description: sub3.description,
      dynamicSpawned: true,
    },
  ];

  const newEdges: FlowEdge[] = [
    {
      id: `e-dyn-${Date.now()}-1`,
      source: parent.stateNodeId || 'node_epic_auth',
      target: sub1.stateNodeId!,
      active: true,
    },
    {
      id: `e-dyn-${Date.now()}-2`,
      source: parent.stateNodeId || 'node_epic_auth',
      target: sub2.stateNodeId!,
      active: true,
    },
    {
      id: `e-dyn-${Date.now()}-3`,
      source: sub1.stateNodeId!,
      target: sub3.stateNodeId!,
      active: false,
    },
    {
      id: `e-dyn-${Date.now()}-4`,
      source: sub2.stateNodeId!,
      target: sub3.stateNodeId!,
      active: false,
    },
    {
      id: `e-dyn-${Date.now()}-5`,
      source: sub3.stateNodeId!,
      target: 'node_integration',
      active: false,
    },
  ];

  return { subItems: [sub1, sub2, sub3], newNodes, newEdges };
}

export function createNewEpicFromPrompt(topic: string): { newEpic: WorkItem; newNode: FlowNode; newEdge: FlowEdge } {
  const timestamp = new Date().toISOString().replace('T', ' ').slice(0, 16);
  const baseNum = Math.floor(Math.random() * 600) + 300;
  const epicId = `WI-${baseNum}`;
  const nodeId = `node_epic_${baseNum}`;

  const newEpic: WorkItem = {
    id: epicId,
    title: topic,
    description: `由 Agent 对话规划生成的主任务（Epic）。包含完整架构设计、子模块拆解与全流程交付闭环。`,
    type: 'epic',
    status: 'todo',
    priority: 'P0',
    assignee: AGENTS.project_agent,
    reporter: AGENTS.user,
    createdAt: timestamp,
    updatedAt: timestamp,
    subItemIds: [],
    isRootEpic: true,
    stateNodeId: nodeId,
    estimatedHours: 32,
    loggedHours: 0,
    progress: 0,
    activePrdVersion: 'v1.0',
    prdVersions: [
      {
        version: 'v1.0',
        title: `${topic} - PRD v1.0`,
        createdAt: timestamp,
        author: AGENTS.project_agent,
        giteaCommit: {
          repo: `gitea.corp.ai/enterprise/${topic.toLowerCase().replace(/[^a-z0-9]/g, '-').slice(0, 16) || 'project'}`,
          branch: 'main',
          commitHash: Math.random().toString(16).slice(2, 9),
          filePath: 'docs/PRD.md',
        },
        summary: `初始版本：根据需求《${topic}》由 PM Agent 编排生成的 PRD 规格基线。`,
        content: `# ${topic} - PRD 需求规格说明书\n\n> 托管仓库：Gitea (main: docs/PRD.md)\n> 编排 Agent：Project Orchestrator Agent\n\n## 1.0 项目背景与业务目标\n${topic} 旨在解决当前系统的关键技术瓶颈与业务诉求，提供高可用、可扩展的工程解法。\n\n## 2.0 用户用例与交互模型\n定义核心系统交互边界与用户关键路径...\n\n## 3.0 API 契约与数据模型\n标准化接口规范与协议定义...\n\n## 4.0 安全规范与质量门禁\n包含多重安全防护、熔断限流机制与 90%+ 单元测试覆盖率门禁。`,
        comments: [],
        isActive: true,
      },
    ],
    tags: ['AI-Planned', 'Epic', 'Core'],
    transitionHistory: [
      {
        id: `th-init-${baseNum}`,
        timestamp,
        fromStatus: 'backlog',
        toStatus: 'todo',
        actor: AGENTS.project_agent,
        reason: `根据对话需求《${topic}》，Project Agent 制定方案并下发主工单。`,
      },
    ],
    commits: [],
    auditLogs: [
      {
        id: `log-epic-${baseNum}-1`,
        timestamp,
        level: 'INFO',
        stepName: '自然语言需求解析与主工单建立 (Intent Synthesis)',
        phase: 'planning',
        executor: AGENTS.project_agent,
        summary: `解析自然语言指令 "${topic}" 并生成主任务规格说明。`,
        durationMs: 160,
        details: {
          inputParams: { prompt: topic },
          stdout: `[INFO] Parsing prompt: "${topic}"\n[SUCCESS] Created root epic ${epicId}`,
          exitCode: 0,
        },
      },
    ],
    itemChatMessages: [
      {
        id: `ic-epic-${baseNum}-1`,
        sender: AGENTS.project_agent,
        content: `我是负责 **[${epicId}] ${topic}** 的 Project Orchestrator Agent。可在下方下达进一步的细化规划或拆解指令。`,
        timestamp: 'Just now',
      },
    ],
  };

  const newNode: FlowNode = {
    id: nodeId,
    title: `[主工单] ${topic.slice(0, 22)}... (${epicId})`,
    type: 'process',
    status: 'idle',
    assignedAgent: AGENTS.project_agent,
    relatedWorkItemId: epicId,
    inputs: ['node_start'],
    outputs: ['node_integration'],
    bottleneck: false,
    duration: 'Standby in Todo',
    x: 320,
    y: 360,
    description: `状态机主干流转节点：${topic}`,
    dynamicSpawned: true,
  };

  const newEdge: FlowEdge = {
    id: `edge-${Date.now()}-${baseNum}`,
    source: 'node_start',
    target: nodeId,
    active: true,
  };

  return { newEpic, newNode, newEdge };
}

