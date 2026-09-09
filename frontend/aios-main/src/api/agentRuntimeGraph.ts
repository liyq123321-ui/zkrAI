import type {
  AgentRuntimeDto,
  AgentSpecDto,
  WorkItemDto,
} from './dto';

export type RuntimeGraphNodeStatus =
  | 'planned'
  | 'ready'
  | 'running'
  | 'completed'
  | 'blocked'
  | 'error';

export type RuntimeGraphProject = {
  sessionId: string;
  title: string;
  workItems?: WorkItemDto[];
  agentSpecs?: AgentSpecDto[];
};

export type RuntimeGraphNode = {
  id: string;
  kind: 'core' | 'auxiliary' | 'task';
  role: string;
  title: string;
  summary: string | null;
  status: RuntimeGraphNodeStatus;
  runtime: AgentRuntimeDto | null;
  workItem: WorkItemDto | null;
};

export type RuntimeGraphEdge = {
  key: string;
  sourceId: string;
  targetId: string;
  status: RuntimeGraphNodeStatus;
};

export type RuntimeAgentGraph = {
  coreLevels: RuntimeGraphNode[][];
  auxiliaryLevels: RuntimeGraphNode[][];
  taskLevels: RuntimeGraphNode[][];
  edges: RuntimeGraphEdge[];
  nodeById: Map<string, RuntimeGraphNode>;
};

type CoreRole = 'pm' | 'reviewer' | 'prototype';

const coreRoles: Array<{
  key: CoreRole;
  role: string;
  title: string;
  matches: (normalizedRole: string) => boolean;
}> = [
  {
    key: 'pm',
    role: 'PM',
    title: '项目规划与任务拆分',
    matches: (role) => role === 'PM' || role.startsWith('PM ') || role.includes('PROJECT_MANAGER') || role.includes('PROJECT MANAGER'),
  },
  {
    key: 'reviewer',
    role: 'REVIEWER',
    title: '规格与拆分结果审核',
    matches: (role) => role.includes('REVIEW'),
  },
  {
    key: 'prototype',
    role: 'PROTOTYPE_DESIGNER',
    title: 'PRD 交互原型生成',
    matches: (role) => role.includes('PROTOTYPE'),
  },
];

function normalizeRole(role: string): string {
  return role.trim().toUpperCase().replaceAll('-', '_');
}

function runtimeStatus(agent: AgentRuntimeDto | undefined, role: CoreRole): RuntimeGraphNodeStatus {
  if (agent) return agent.status;
  return role === 'pm' ? 'ready' : 'planned';
}

function taskStatus(status: string | null | undefined): RuntimeGraphNodeStatus {
  switch ((status ?? 'todo').toLowerCase()) {
    case 'in_progress':
    case 'running':
    case 'started':
      return 'running';
    case 'completed':
    case 'complete':
    case 'done':
    case 'succeeded':
      return 'completed';
    case 'failed':
    case 'error':
      return 'error';
    case 'blocked':
      return 'blocked';
    default:
      return 'planned';
  }
}

function taskAssignee(task: WorkItemDto, spec: AgentSpecDto): string {
  const content = spec.content;
  const fromSpec = content.suggested_assignee ?? content.responsible_role;
  return task.suggested_assignee
    || task.responsible_role
    || (typeof fromSpec === 'string' ? fromSpec : null)
    || '待分配子 Agent';
}

function taskDepth(
  taskId: string,
  tasks: Map<string, WorkItemDto>,
  cache: Map<string, number>,
  visiting = new Set<string>(),
): number {
  const cached = cache.get(taskId);
  if (cached !== undefined) return cached;
  if (visiting.has(taskId)) return 0;
  const task = tasks.get(taskId);
  if (!task) return 0;
  const nextVisiting = new Set(visiting).add(taskId);
  const dependencyDepths = task.dependency_work_item_ids
    .filter((dependencyId) => tasks.has(dependencyId))
    .map((dependencyId) => taskDepth(dependencyId, tasks, cache, nextVisiting));
  const depth = dependencyDepths.length === 0 ? 0 : Math.max(...dependencyDepths) + 1;
  cache.set(taskId, depth);
  return depth;
}

export function buildRuntimeAgentGraph(
  project: RuntimeGraphProject,
  agents: AgentRuntimeDto[],
): RuntimeAgentGraph {
  const claimedAgentIds = new Set<string>();
  const coreNodes = coreRoles.map((definition) => {
    const agent = agents.find((candidate) => (
      !claimedAgentIds.has(candidate.agent_session_id)
      && definition.matches(normalizeRole(candidate.role))
    ));
    if (agent) claimedAgentIds.add(agent.agent_session_id);
    return {
      id: `core:${definition.key}`,
      kind: 'core' as const,
      role: agent?.role ?? definition.role,
      title: agent?.purpose ?? definition.title,
      summary: agent?.current_summary ?? null,
      status: runtimeStatus(agent, definition.key),
      runtime: agent ?? null,
      workItem: null,
    };
  });

  const auxiliaryNodes = agents
    .filter((agent) => !claimedAgentIds.has(agent.agent_session_id))
    .map((agent) => ({
      id: `runtime:${agent.agent_session_id}`,
      kind: 'auxiliary' as const,
      role: agent.role,
      title: agent.purpose ?? agent.current_operation,
      summary: agent.current_summary,
      status: agent.status,
      runtime: agent,
      workItem: null,
    }));

  const workItemById = new Map(
    (project.workItems ?? [])
      .filter((item) => item.kind === 'TASK')
      .map((item) => [item.id, item]),
  );
  const taskNodes = (project.agentSpecs ?? []).flatMap((spec) => {
    const task = workItemById.get(spec.work_item_id);
    if (!task) return [];
    return [{
      id: `task:${task.id}`,
      kind: 'task' as const,
      role: taskAssignee(task, spec),
      title: task.title || task.objective || task.id,
      summary: task.objective ?? null,
      status: taskStatus(task.status),
      runtime: null,
      workItem: task,
    }];
  });

  const taskNodeByWorkItemId = new Map(
    taskNodes.map((node) => [node.workItem!.id, node]),
  );
  const depthCache = new Map<string, number>();
  const groupedTaskNodes = new Map<number, RuntimeGraphNode[]>();
  for (const node of taskNodes) {
    const depth = taskDepth(node.workItem!.id, workItemById, depthCache);
    groupedTaskNodes.set(depth, [...(groupedTaskNodes.get(depth) ?? []), node]);
  }
  const taskLevels = [...groupedTaskNodes.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, nodes]) => nodes);

  const nodeById = new Map(
    [...coreNodes, ...auxiliaryNodes, ...taskNodes].map((node) => [node.id, node]),
  );
  const edges: RuntimeGraphEdge[] = [];
  const addEdge = (sourceId: string, targetId: string) => {
    const target = nodeById.get(targetId);
    if (!target) return;
    edges.push({
      key: `${sourceId}->${targetId}`,
      sourceId,
      targetId,
      status: target.status,
    });
  };

  addEdge('core:pm', 'core:reviewer');
  addEdge('core:pm', 'core:prototype');
  addEdge('core:reviewer', 'core:prototype');
  auxiliaryNodes.forEach((node) => addEdge('core:pm', node.id));
  for (const node of taskNodes) {
    const dependencies = node.workItem!.dependency_work_item_ids
      .map((dependencyId) => taskNodeByWorkItemId.get(dependencyId))
      .filter((dependency) => dependency !== undefined);
    if (dependencies.length === 0) {
      addEdge('core:pm', node.id);
      addEdge('core:prototype', node.id);
    }
    else dependencies.forEach((dependency) => addEdge(dependency.id, node.id));
  }

  return {
    coreLevels: coreNodes.map((node) => [node]),
    auxiliaryLevels: auxiliaryNodes.length > 0 ? [auxiliaryNodes] : [],
    taskLevels,
    edges,
    nodeById,
  };
}
