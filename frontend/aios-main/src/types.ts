export type WorkItemStatus = 'backlog' | 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked';

export type WorkItemPriority = 'P0' | 'P1' | 'P2' | 'P3';

export type AgentRole = 
  | 'project_agent' 
  | 'architect_agent' 
  | 'backend_agent' 
  | 'frontend_agent' 
  | 'qa_agent' 
  | 'security_agent'
  | 'user';

export interface AgentInfo {
  id: string;
  name: string;
  role: AgentRole;
  avatar: string;
  color: string;
  specialty: string;
}

export interface TransitionHistory {
  id: string;
  timestamp: string;
  fromStatus: WorkItemStatus;
  toStatus: WorkItemStatus;
  actor: AgentInfo;
  reason: string;
  notes?: string;
  durationInPrevState?: string;
}

export interface GitCommitRecord {
  id: string;
  hash: string;
  shortHash: string;
  message: string;
  author: AgentInfo;
  timestamp: string;
  filesChanged: number;
  insertions: number;
  deletions: number;
  files: {
    name: string;
    status: 'added' | 'modified' | 'deleted';
    diffSnippet: string;
  }[];
}

export type LogLevel = 'INFO' | 'DEBUG' | 'WARN' | 'ERROR' | 'SUCCESS';

export interface ExecutionAuditLog {
  id: string;
  timestamp: string;
  level: LogLevel;
  stepName: string;
  phase: 'planning' | 'codegen' | 'compilation' | 'testing' | 'security_scan' | 'deployment' | 'review' | 'runtime';
  executor: AgentInfo;
  summary: string;
  durationMs: number;
  details: {
    inputParams?: Record<string, any>;
    stdout?: string;
    outputResult?: Record<string, any>;
    stackTrace?: string;
    exitCode?: number;
    rawPayload?: string;
    assertions?: { name: string; status: 'passed' | 'failed' | 'skipped'; durationMs: number }[];
  };
}

export interface PRDComment {
  id: string;
  sectionId: string;
  sectionTitle: string;
  targetQuote: string;
  content: string;
  author: AgentInfo;
  createdAt: string;
  resolved: boolean;
  resolutionNote?: string;
}

export interface PRDVersion {
  version: string; // e.g. 'v1.0', 'v1.1'
  title: string;
  createdAt: string;
  author: AgentInfo;
  giteaCommit: {
    repo: string;
    branch: string;
    commitHash: string;
    commitUrl?: string;
    filePath: string;
  };
  summary: string;
  content: string;
  comments: PRDComment[];
  isActive: boolean;
}

export interface WorkItem {
  id: string; // e.g. WI-101
  title: string;
  description: string;
  type: 'epic' | 'story' | 'task' | 'bug' | 'subtask';
  status: WorkItemStatus;
  priority: WorkItemPriority;
  assignee: AgentInfo;
  reporter: AgentInfo;
  createdAt: string;
  updatedAt: string;
  parentId?: string; // Derived from which work item
  parentTitle?: string;
  subItemIds: string[]; // Child work items spawned
  transitionHistory: TransitionHistory[];
  commits: GitCommitRecord[];
  auditLogs: ExecutionAuditLog[];
  itemChatMessages?: AgentChatMessage[];
  prdVersions?: PRDVersion[];
  activePrdVersion?: string;
  tags: string[];
  estimatedHours: number;
  loggedHours: number;
  progress: number; // 0 to 100
  isRootEpic?: boolean;
  stateNodeId?: string; // Connected node in State Machine graph
  bottleneckReason?: string;
}

export type FlowNodeStatus = 'idle' | 'running' | 'completed' | 'blocked' | 'failed' | 'waiting_approval';

export interface FlowNode {
  id: string;
  title: string;
  type: 'start' | 'process' | 'condition' | 'agent_action' | 'review' | 'deploy' | 'end';
  status: FlowNodeStatus;
  assignedAgent: AgentInfo;
  relatedWorkItemId?: string;
  inputs: string[]; // input node IDs
  outputs: string[]; // output node IDs
  bottleneck: boolean;
  bottleneckMsg?: string;
  duration: string;
  latencyMs?: number;
  x: number;
  y: number;
  description: string;
  dynamicSpawned?: boolean;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  active?: boolean;
  isBottleneck?: boolean;
}

export interface AgentChatMessage {
  id: string;
  sender: AgentInfo;
  content: string;
  timestamp: string;
  thoughtChain?: string[];
  actionType?: 'propose_task' | 'spawn_subtasks' | 'code_commit' | 'state_change' | 'general' | 'exec_command' | 'log_update';
  actionPayload?: {
    createdWorkItemId?: string;
    spawnedSubItemIds?: string[];
    affectedNodeId?: string;
    logEntryId?: string;
  };
}


