import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Crosshair, Minus, Network, Plus, Search, SearchCheck, X } from 'lucide-react';
import type { AgentSpecDto, SpecVersionDto, WorkItemDto } from './dto';
import type { TopLevelPhase } from './TopLevelGraph';
import { workItemStatusLabel } from './workflowUi';

type MindNodeKind = 'project' | 'spec' | 'requirement' | 'phase' | 'milestone' | 'workitem' | 'owner' | 'deliverable' | 'testcase' | 'agent-spec' | 'skill';
type MindEdgeKind = 'tree' | 'dependency' | 'trace';

export type ProjectMindNode = {
  id: string;
  kind: MindNodeKind;
  title: string;
  subtitle: string;
  detail: string;
  parentId: string | null;
  depth: number;
  workItemId?: string;
};

export type ProjectMindEdge = {
  id: string;
  from: string;
  to: string;
  kind: MindEdgeKind;
  label?: string;
};

export type ProjectMindGraph = {
  nodes: ProjectMindNode[];
  edges: ProjectMindEdge[];
};

type PositionedNode = ProjectMindNode & { x: number; y: number };

const NODE_WIDTH = 280;
const NODE_HEIGHT = 92;
const X_STEP = 350;
const Y_STEP = 120;
const VECTOR_SIZE = 384;
const DEFAULT_VISIBLE_DEPTH = 1;

const domainAliases: Record<string, string[]> = {
  '测试': ['tc', '验收', '用例', '验证'], 'tc': ['测试', '验收', '用例'], '验收': ['tc', '测试', '预期结果'],
  '交付物': ['产物', '输出', 'deliverable'], '产物': ['交付物', '输出'], '输出': ['交付物', '产物'],
  '负责人': ['owner', '责任人', '角色', 'assignee'], '责任人': ['负责人', 'owner'], '角色': ['负责人', 'owner'],
  '需求': ['spec', 'requirement', 'fr', 'nfr'], 'spec': ['需求', '规格'], '任务': ['workitem', '工单'],
  'workitem': ['任务', '工单'], '依赖': ['前置', 'dependency'], '阶段': ['phase', '里程碑'],
  '技能': ['skill', '能力', '装配'], 'skill': ['技能', '能力', '装配'],
};

function hashToken(token: string) {
  let hash = 2166136261;
  for (let index = 0; index < token.length; index += 1) hash = Math.imul(hash ^ token.charCodeAt(index), 16777619);
  return Math.abs(hash) % VECTOR_SIZE;
}

function semanticTokens(value: string) {
  const normalized = value.toLocaleLowerCase().replace(/[\s\p{P}\p{S}]+/gu, ' ').trim();
  const tokens = normalized.split(' ').filter(Boolean);
  const chinese = normalized.match(/[\u3400-\u9fff]+/g) ?? [];
  for (const group of chinese) {
    if (group.length <= 3) tokens.push(group);
    for (let index = 0; index < group.length - 1; index += 1) tokens.push(group.slice(index, index + 2));
    for (let index = 0; index < group.length - 2; index += 1) tokens.push(group.slice(index, index + 3));
  }
  const expanded = [...tokens];
  for (const token of tokens) for (const [term, aliases] of Object.entries(domainAliases)) {
    if (token.includes(term) || term.includes(token)) expanded.push(...aliases);
  }
  return expanded;
}

function textVector(value: string) {
  const vector = new Float32Array(VECTOR_SIZE);
  for (const token of semanticTokens(value)) vector[hashToken(token)] += token.length > 2 ? 1.35 : 1;
  let length = 0;
  for (const component of vector) length += component * component;
  const norm = Math.sqrt(length) || 1;
  return vector.map((component) => component / norm);
}

function cosineSimilarity(left: Float32Array, right: Float32Array) {
  let score = 0;
  for (let index = 0; index < VECTOR_SIZE; index += 1) score += left[index] * right[index];
  return score;
}

function isExplicitIdentityQuery(value: string) {
  return /^[a-z][a-z0-9]*-\d+$/i.test(value)
    || /^(?:owner|agent)-[a-z0-9][a-z0-9._-]*$/i.test(value)
    || /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function nodeIdentityValues(node: ProjectMindNode) {
  return [node.title, node.subtitle, node.workItemId]
    .filter((value): value is string => Boolean(value))
    .flatMap((value) => value.toLocaleLowerCase().split(/\s*·\s*/).map((part) => part.trim()).filter(Boolean));
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function compact(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value).trim();
  const item = record(value);
  if (!item) return '';
  const preferred = ['title', 'name', 'criterion', 'statement', 'description', 'expected_result', 'output', 'path', 'id'];
  const values = preferred.map((key) => compact(item[key])).filter(Boolean);
  if (values.length) return values.join(' · ');
  return Object.values(item).map(compact).filter(Boolean).slice(0, 3).join(' · ');
}

function detailText(value: unknown): string {
  const item = record(value);
  if (!item) return compact(value);
  return [compact(item.verification_method), compact(item.expected_result), compact(item.description)]
    .filter(Boolean).join(' · ') || compact(value);
}

function requirementEntries(spec: SpecVersionDto | null) {
  if (!spec) return [];
  return [...list(spec.content.functional_requirements), ...list(spec.content.non_functional_requirements)]
    .map(record).filter((item): item is Record<string, unknown> => Boolean(item))
    .map((item, index) => ({
      id: compact(item.requirement_id) || `REQ-${index + 1}`,
      statement: compact(item.statement) || compact(item.description) || '未命名需求',
      priority: compact(item.priority),
    }));
}

function acceptanceEntries(value: unknown) {
  return list(value).map((item, index) => ({
    id: compact(record(item)?.test_case_id) || compact(record(item)?.acceptance_id) || `TC-${String(index + 1).padStart(3, '0')}`,
    title: compact(record(item)?.criterion) || compact(item) || `验收用例 ${index + 1}`,
    detail: detailText(item),
    requirementIds: list(record(item)?.requirement_ids).map(compact).filter(Boolean),
  }));
}

function mergeAcceptanceEntries(...values: unknown[]) {
  const seen = new Set<string>();
  return values.flatMap(acceptanceEntries).filter((entry) => {
    const key = JSON.stringify([entry.title, entry.detail, [...entry.requirementIds].sort()]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function referencedRequirementIds(value: unknown, knownIds: Map<string, string>) {
  const result = new Set<string>();
  const visit = (candidate: unknown, field = '') => {
    if (Array.isArray(candidate)) {
      if (/^requirement_ids?$/i.test(field)) {
        for (const item of candidate) {
          const canonical = knownIds.get(compact(item).toLocaleLowerCase());
          if (canonical) result.add(canonical);
        }
        return;
      }
      candidate.forEach((item) => visit(item));
      return;
    }
    const item = record(candidate);
    if (item) {
      Object.entries(item).forEach(([key, nested]) => visit(nested, key));
      return;
    }
    if (/^requirement_id$/i.test(field)) {
      const canonical = knownIds.get(compact(candidate).toLocaleLowerCase());
      if (canonical) result.add(canonical);
    }
  };
  visit(value);
  return result;
}

function assignee(item: WorkItemDto): string {
  return item.suggested_assignee || item.responsible_role || '待分配';
}

function decomposedSpec(specs: SpecVersionDto[], agentSpecs: AgentSpecDto[]): SpecVersionDto | null {
  const sourceCounts = new Map<string, number>();
  for (const agentSpec of agentSpecs) {
    sourceCounts.set(agentSpec.source_spec_version_id, (sourceCounts.get(agentSpec.source_spec_version_id) ?? 0) + 1);
  }
  const sourceId = [...sourceCounts.entries()].sort((left, right) => right[1] - left[1])[0]?.[0];
  return specs.find((spec) => spec.id === sourceId)
    ?? [...specs].sort((left, right) => right.revision - left.revision)[0]
    ?? null;
}

export function buildProjectMindGraph({ projectTitle, specs, phases, workItems, agentSpecs }: {
  projectTitle: string;
  specs: SpecVersionDto[];
  phases: TopLevelPhase[];
  workItems: WorkItemDto[];
  agentSpecs: AgentSpecDto[];
}): ProjectMindGraph {
  const nodes: ProjectMindNode[] = [];
  const edges: ProjectMindEdge[] = [];
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  const addEdge = (edge: ProjectMindEdge) => {
    if (edgeIds.has(edge.id)) return;
    edgeIds.add(edge.id);
    edges.push(edge);
  };
  const addNode = (node: ProjectMindNode, treeLabel?: string) => {
    if (nodeIds.has(node.id)) return;
    nodeIds.add(node.id);
    nodes.push(node);
    if (node.parentId) addEdge({ id: `${node.parentId}->${node.id}`, from: node.parentId, to: node.id, kind: 'tree', label: treeLabel });
  };
  const rootId = 'project:root';
  addNode({ id: rootId, kind: 'project', title: projectTitle, subtitle: '当前项目任务知识图谱', detail: '完全依据当前 Spec、SDLC、WorkItem 与 Agent Spec 数据生成。', parentId: null, depth: 0 });

  const sourceSpec = decomposedSpec(specs, agentSpecs);
  const requirements = requirementEntries(sourceSpec);
  const requirementNodeById = new Map<string, string>();
  const canonicalRequirementIds = new Map(requirements.map((requirement) => [requirement.id.toLocaleLowerCase(), requirement.id]));
  if (sourceSpec) {
    const specId = `spec:${sourceSpec.id}`;
    const decompositionCount = agentSpecs.filter((agentSpec) => agentSpec.source_spec_version_id === sourceSpec.id).length;
    addNode({ id: specId, kind: 'spec', title: `Spec v${sourceSpec.revision}`, subtitle: sourceSpec.status,
      detail: `${sourceSpec.change_summary || '规格基线'}${decompositionCount ? ` · 已拆分为 ${decompositionCount} 个 Agent Spec` : ''}`, parentId: rootId, depth: 1 });
    for (const requirement of requirements) {
      const nodeId = `requirement:${requirement.id}`;
      requirementNodeById.set(requirement.id, nodeId);
      addNode({ id: nodeId, kind: 'requirement', title: requirement.id, subtitle: requirement.statement, detail: requirement.priority ? `优先级：${requirement.priority}` : '规格需求', parentId: specId, depth: 2 });
    }
    for (const [index, testcase] of acceptanceEntries(sourceSpec.content.acceptance_criteria).entries()) {
      const parents = testcase.requirementIds.map((id) => requirementNodeById.get(id)).filter((id): id is string => Boolean(id));
      const parentId = parents[0] ?? specId;
      const tcId = `spec-tc:${index}`;
      addNode({ id: tcId, kind: 'testcase', title: testcase.id, subtitle: testcase.title, detail: testcase.detail, parentId, depth: parents.length ? 3 : 2 }, parents.length ? '验收验证' : undefined);
      for (const extraParent of parents.slice(1)) addEdge({ id: `${extraParent}->${tcId}:trace`, from: extraParent, to: tcId, kind: 'trace', label: '验收验证' });
    }
  }

  const representedWorkItems = new Set<string>();
  const workItemNodeById = new Map<string, string>();
  const addWorkItemDetails = (item: WorkItemDto, parentId: string, depth: number, phaseDeliverables: unknown[] = []) => {
    const nodeId = `workitem:${item.id}`;
    representedWorkItems.add(item.id);
    workItemNodeById.set(item.id, nodeId);
    const agentSpec = agentSpecs.find((candidate) => candidate.work_item_id === item.id);
    addNode({ id: nodeId, kind: item.kind === 'MILESTONE' ? 'milestone' : 'workitem', title: item.title || item.local_key || item.id,
      subtitle: `${item.local_key || item.id} · ${workItemStatusLabel(item.status)}`,
      detail: item.objective || item.description || item.summary || '暂无任务说明', parentId, depth, workItemId: item.id });
    const owner = assignee(item);
    addNode({ id: `owner:${item.id}`, kind: 'owner', title: '负责人', subtitle: owner,
      detail: item.responsible_role && item.responsible_role !== owner ? `责任角色：${item.responsible_role}` : '当前任务分配', parentId: nodeId, depth: depth + 1 });
    const outputs = [...list(item.outputs), ...phaseDeliverables];
    outputs.forEach((output, index) => addNode({ id: `deliverable:${item.id}:${index}`, kind: 'deliverable', title: `交付物 ${index + 1}`,
      subtitle: compact(output) || '未命名交付物', detail: detailText(output), parentId: nodeId, depth: depth + 1 }));
    const requirementIds = referencedRequirementIds({ workItem: item, agentSpec: agentSpec?.content }, canonicalRequirementIds);
    for (const requirementId of requirementIds) {
      const requirementNodeId = requirementNodeById.get(requirementId);
      if (requirementNodeId) addEdge({ id: `${requirementNodeId}->${nodeId}:implements`, from: requirementNodeId, to: nodeId, kind: 'trace', label: '任务实现' });
    }
    const agentContent = record(agentSpec?.content);
    mergeAcceptanceEntries(item.acceptance_criteria, agentContent?.acceptance_criteria).forEach((testcase, index) => {
      const testcaseId = `workitem-tc:${item.id}:${index}`;
      addNode({ id: testcaseId, kind: 'testcase', title: testcase.id || `TC-${String(index + 1).padStart(3, '0')}`,
        subtitle: testcase.title, detail: testcase.detail, parentId: nodeId, depth: depth + 1 }, '任务验收');
      for (const requirementId of testcase.requirementIds) {
        const canonicalId = canonicalRequirementIds.get(requirementId.toLocaleLowerCase());
        const requirementNodeId = canonicalId ? requirementNodeById.get(canonicalId) : undefined;
        if (requirementNodeId) addEdge({ id: `${requirementNodeId}->${testcaseId}:verifies`, from: requirementNodeId, to: testcaseId, kind: 'trace', label: '验收验证' });
      }
    });
    const agentSpecNodeId = agentSpec ? `agent-spec:${agentSpec.id}` : null;
    if (agentSpec && agentSpecNodeId) addNode({ id: agentSpecNodeId, kind: 'agent-spec', title: 'Agent Spec',
      subtitle: agentSpec.id, detail: `来源 Spec：${agentSpec.source_spec_version_id} · 依赖 ${agentSpec.dependency_work_item_ids.length} 项`, parentId: nodeId, depth: depth + 1 });
    const skills = [...new Set([
      ...list(item.required_skills).map(compact),
      ...list(record(agentSpec?.content)?.required_skills).map(compact),
    ].filter(Boolean))];
    skills.forEach((skill, index) => addNode({ id: `skill:${item.id}:${index}`, kind: 'skill', title: skill,
      subtitle: 'Agent 装配技能', detail: agentSpec ? `由 Agent Spec ${agentSpec.id} 约束` : '由 WorkItem 约束',
      parentId: agentSpecNodeId ?? nodeId, depth: depth + (agentSpecNodeId ? 2 : 1) }));
  };

  for (const [phaseIndex, phase] of phases.entries()) {
    const phaseId = `phase:${phase.iteration}:${phase.stage_id}`;
    const milestone = phase.milestone;
    const phaseOwner = milestone ? assignee(milestone) : phase.stage.owner;
    addNode({ id: phaseId, kind: 'phase', title: `阶段 ${phase.stage.number} · ${phase.stage.name}`,
      subtitle: phase.stage.objective, detail: `负责人：${phaseOwner} · 阶段角色：${phase.stage.owner} · 排期：${phase.schedule || phase.stage.schedule_guidance}`,
      parentId: rootId, depth: 1 });
    const milestoneId = milestone ? `workitem:${milestone.id}` : `milestone:${phaseId}`;
    if (milestone) addWorkItemDetails(milestone, phaseId, 2, phase.stage.deliverables);
    else addNode({ id: milestoneId, kind: 'milestone', title: phase.stage.name, subtitle: phase.milestone_key,
      detail: phase.stage.exit_criteria, parentId: phaseId, depth: 2 });
    for (const task of phase.tasks) addWorkItemDetails(task, milestoneId, 3);
    if (!phase.tasks.length && !milestone && phase.stage.deliverables.length) {
      phase.stage.deliverables.forEach((output, index) => addNode({ id: `phase-deliverable:${phaseIndex}:${index}`, kind: 'deliverable',
        title: `阶段交付物 ${index + 1}`, subtitle: output.name, detail: output.id, parentId: phaseId, depth: 2 }));
    }
  }

  const remaining = workItems.filter((item) => item.kind !== 'ROOT' && !representedWorkItems.has(item.id));
  if (remaining.length) {
    const ungroupedId = 'phase:ungrouped';
    addNode({ id: ungroupedId, kind: 'phase', title: '其他 WorkItem', subtitle: '尚未映射到 SDLC 阶段', detail: `${remaining.length} 项`, parentId: rootId, depth: 1 });
    for (const item of remaining) addWorkItemDetails(item, ungroupedId, 2);
  }

  for (const item of workItems) {
    const target = workItemNodeById.get(item.id);
    if (!target) continue;
    for (const dependencyId of item.dependency_work_item_ids) {
      const source = workItemNodeById.get(dependencyId);
      if (source) addEdge({ id: `${source}->${target}:dependency`, from: source, to: target, kind: 'dependency', label: '前置依赖' });
    }
  }
  return { nodes, edges };
}

function positionGraph(graph: ProjectMindGraph) {
  const children = new Map<string, string[]>();
  for (const edge of graph.edges.filter((item) => item.kind === 'tree')) {
    children.set(edge.from, [...(children.get(edge.from) ?? []), edge.to]);
  }
  const positions = new Map<string, { x: number; y: number }>();
  let leafIndex = 0;
  const visit = (id: string): number => {
    const childIds = children.get(id) ?? [];
    let y: number;
    if (!childIds.length) {
      y = 42 + leafIndex * Y_STEP;
      leafIndex += 1;
    } else {
      const childYs = childIds.map(visit);
      y = (childYs[0] + childYs[childYs.length - 1]) / 2;
    }
    const node = graph.nodes.find((item) => item.id === id)!;
    positions.set(id, { x: 42 + node.depth * X_STEP, y });
    return y;
  };
  visit('project:root');
  const nodes = graph.nodes.map((node) => ({ ...node, ...(positions.get(node.id) ?? { x: 42 + node.depth * X_STEP, y: 42 }) }));
  const width = Math.max(1120, ...nodes.map((node) => node.x + NODE_WIDTH + 54));
  const height = Math.max(680, ...nodes.map((node) => node.y + NODE_HEIGHT + 42));
  return { nodes, width, height };
}

const kindLabels: Record<MindNodeKind, string> = {
  project: 'PROJECT', spec: 'SPEC', requirement: 'REQ', phase: 'PHASE', milestone: 'MILESTONE', workitem: 'WORKITEM',
  owner: 'OWNER', deliverable: 'DELIVERABLE', testcase: 'TC / AC', 'agent-spec': 'AGENT SPEC', skill: 'SKILL',
};

function relatedNodeIds(graph: ProjectMindGraph, seeds: Set<string>, includeChildren: boolean) {
  const result = new Set(seeds);
  const parentByChild = new Map(graph.edges.filter((edge) => edge.kind === 'tree').map((edge) => [edge.to, edge.from]));
  for (const seed of seeds) {
    let parent = parentByChild.get(seed);
    while (parent) { result.add(parent); parent = parentByChild.get(parent); }
  }
  if (includeChildren) {
    for (const edge of graph.edges) {
      if (seeds.has(edge.from)) result.add(edge.to);
      if (seeds.has(edge.to)) result.add(edge.from);
    }
  }
  return result;
}

function traceabilityNeighborhood(graph: ProjectMindGraph, seeds: Set<string>) {
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const traceKinds = new Set<MindNodeKind>(['requirement', 'workitem', 'testcase']);
  const result = new Set<string>();
  const linked = (id: string, acceptedKinds: Set<MindNodeKind>, edgeKinds: Set<ProjectMindEdge['kind']>) => {
    for (const edge of graph.edges) {
      if (!edgeKinds.has(edge.kind) || (edge.from !== id && edge.to !== id)) continue;
      const otherId = edge.from === id ? edge.to : edge.from;
      const otherKind = nodeById.get(otherId)?.kind;
      if (otherKind && acceptedKinds.has(otherKind)) result.add(otherId);
    }
  };
  for (const seed of seeds) {
    const seedKind = nodeById.get(seed)?.kind;
    if (!seedKind || !traceKinds.has(seedKind)) continue;
    result.add(seed);
    if (seedKind === 'requirement') {
      linked(seed, new Set(['workitem', 'testcase']), new Set(['trace']));
      for (const id of [...result]) {
        if (nodeById.get(id)?.kind === 'workitem') linked(id, new Set(['testcase']), new Set(['tree']));
      }
    } else if (seedKind === 'workitem') {
      linked(seed, new Set(['requirement']), new Set(['trace']));
      linked(seed, new Set(['testcase']), new Set(['tree']));
    } else {
      linked(seed, new Set(['requirement']), new Set(['trace']));
      linked(seed, new Set(['workitem']), new Set(['tree']));
    }
  }
  return result;
}

function treeMaps(graph: ProjectMindGraph) {
  const parentByChild = new Map<string, string>();
  const childrenByParent = new Map<string, string[]>();
  for (const edge of graph.edges.filter((item) => item.kind === 'tree')) {
    parentByChild.set(edge.to, edge.from);
    childrenByParent.set(edge.from, [...(childrenByParent.get(edge.from) ?? []), edge.to]);
  }
  return { parentByChild, childrenByParent };
}

function addAncestors(target: Set<string>, id: string, parentByChild: Map<string, string>) {
  let current: string | undefined = id;
  while (current) {
    target.add(current);
    current = parentByChild.get(current);
  }
}

function addSubtree(target: Set<string>, id: string, childrenByParent: Map<string, string[]>) {
  target.add(id);
  for (const childId of childrenByParent.get(id) ?? []) addSubtree(target, childId, childrenByParent);
}

function visibleNodeIds(graph: ProjectMindGraph, expandedIds: Set<string>, matches: Set<string>, personOwnerIds: Set<string>, traceFocusIds: Set<string>, focusMode: boolean) {
  const { parentByChild, childrenByParent } = treeMaps(graph);
  const visible = new Set(graph.nodes.filter((node) => focusMode ? node.kind === 'project' : node.depth <= DEFAULT_VISIBLE_DEPTH).map((node) => node.id));

  const revealExpandedChildren = (id: string) => {
    if (!expandedIds.has(id)) return;
    for (const childId of childrenByParent.get(id) ?? []) {
      visible.add(childId);
      revealExpandedChildren(childId);
    }
  };
  if (!focusMode) for (const id of [...visible]) revealExpandedChildren(id);

  for (const id of matches) addAncestors(visible, id, parentByChild);
  for (const id of traceFocusIds) addAncestors(visible, id, parentByChild);
  for (const ownerId of personOwnerIds) {
    const workItemId = parentByChild.get(ownerId);
    if (!workItemId) continue;
    addAncestors(visible, workItemId, parentByChild);
    addSubtree(visible, workItemId, childrenByParent);
    for (const edge of graph.edges.filter((candidate) => candidate.kind === 'trace' && (candidate.from === workItemId || candidate.to === workItemId))) {
      const requirementId = edge.from === workItemId ? edge.to : edge.from;
      addAncestors(visible, requirementId, parentByChild);
      addSubtree(visible, requirementId, childrenByParent);
    }
  }
  return visible;
}

export function ProjectMindMap({ projectTitle, specs, phases, workItems, agentSpecs, onClose, onOpenWorkItem }: {
  projectTitle: string;
  specs: SpecVersionDto[];
  phases: TopLevelPhase[];
  workItems: WorkItemDto[];
  agentSpecs: AgentSpecDto[];
  onClose: () => void;
  onOpenWorkItem: (workItemId: string) => void;
}) {
  const [queryInput, setQueryInput] = useState('');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [zoom, setZoom] = useState(1);
  const [centerRequest, setCenterRequest] = useState(0);
  const [searchRequest, setSearchRequest] = useState(0);
  const viewportRef = useRef<HTMLDivElement>(null);
  const centeredRequestRef = useRef(-1);
  const centeredSearchRequestRef = useRef(-1);
  const graph = useMemo(() => buildProjectMindGraph({ projectTitle, specs, phases, workItems, agentSpecs }), [projectTitle, specs, phases, workItems, agentSpecs]);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const identityQuery = isExplicitIdentityQuery(normalizedQuery);
  const exactIdentityIds = useMemo(() => new Set(graph.nodes.filter((node) => normalizedQuery
    && nodeIdentityValues(node).includes(normalizedQuery)).map((node) => node.id)), [graph.nodes, normalizedQuery]);
  const exactOwnerIds = useMemo(() => new Set(graph.nodes.filter((node) => node.kind === 'owner'
    && exactIdentityIds.has(node.id)).map((node) => node.id)), [exactIdentityIds, graph.nodes]);
  const vectorMatches = useMemo(() => {
    if (!normalizedQuery) return new Map<string, number>();
    if (identityQuery) return new Map([...exactIdentityIds].map((id) => [id, 1]));
    const queryVector = textVector(normalizedQuery);
    const ranked = graph.nodes.map((node) => {
      const text = `${node.title} ${node.subtitle} ${node.detail} ${kindLabels[node.kind]}`.toLocaleLowerCase();
      const exact = text.includes(normalizedQuery);
      const score = exact ? 1 : cosineSimilarity(queryVector, textVector(text));
      return [node.id, score] as const;
    }).filter(([, score]) => score >= 0.16).sort((left, right) => right[1] - left[1]).slice(0, 48);
    return new Map(ranked);
  }, [exactIdentityIds, graph.nodes, identityQuery, normalizedQuery]);
  const matches = useMemo(() => new Set(vectorMatches.keys()), [vectorMatches]);
  const personOwnerIds = useMemo(() => identityQuery ? exactOwnerIds : new Set(graph.nodes.filter((node) => node.kind === 'owner' && normalizedQuery
    && `${node.subtitle} ${node.detail}`.toLocaleLowerCase().includes(normalizedQuery)).map((node) => node.id)), [exactOwnerIds, graph.nodes, identityQuery, normalizedQuery]);
  const displayMatches = personOwnerIds.size ? personOwnerIds : matches;
  const traceSeedIds = useMemo(() => selectedId ? new Set([selectedId]) : displayMatches, [displayMatches, selectedId]);
  const traceFocusIds = useMemo(() => traceabilityNeighborhood(graph, traceSeedIds), [graph, traceSeedIds]);
  const visibleIds = useMemo(() => visibleNodeIds(graph, expandedIds, displayMatches, personOwnerIds, traceFocusIds, Boolean(normalizedQuery || traceFocusIds.size)), [displayMatches, expandedIds, graph, normalizedQuery, personOwnerIds, traceFocusIds]);
  const visibleGraph = useMemo(() => ({
    nodes: graph.nodes.filter((node) => visibleIds.has(node.id)),
    edges: graph.edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to)),
  }), [graph, visibleIds]);
  const layout = useMemo(() => positionGraph(visibleGraph), [visibleGraph]);
  const nodeById = useMemo(() => new Map(layout.nodes.map((node) => [node.id, node])), [layout.nodes]);
  const childrenByParent = useMemo(() => treeMaps(graph).childrenByParent, [graph]);
  const activeIds = useMemo(() => personOwnerIds.size ? visibleIds : traceFocusIds.size ? relatedNodeIds(graph, traceFocusIds, false)
    : normalizedQuery ? relatedNodeIds(graph, matches, true)
      : selectedId ? relatedNodeIds(graph, new Set([selectedId]), true) : new Set<string>(), [graph, matches, normalizedQuery, personOwnerIds.size, selectedId, traceFocusIds, visibleIds]);
  const selected = selectedId ? nodeById.get(selectedId) ?? null : null;
  const selectedTraceNodes = useMemo(() => {
    if (!selectedId) return [];
    const relationIds = traceabilityNeighborhood(graph, new Set([selectedId]));
    return graph.nodes.filter((node) => node.id !== selectedId && relationIds.has(node.id)
      && ['requirement', 'workitem', 'testcase'].includes(node.kind));
  }, [graph, selectedId]);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || centeredRequestRef.current === centerRequest) return;
    centeredRequestRef.current = centerRequest;
    viewport.scrollLeft = Math.max(0, (layout.width * zoom - viewport.clientWidth) / 2);
    viewport.scrollTop = Math.max(0, (layout.height * zoom - viewport.clientHeight) / 2);
  }, [centerRequest, layout.height, layout.width, nodeById, zoom]);

  useEffect(() => {
    if (!normalizedQuery || centeredSearchRequestRef.current === searchRequest) return;
    centeredSearchRequestRef.current = searchRequest;
    const viewport = viewportRef.current;
    const firstMatchId = displayMatches.values().next().value as string | undefined;
    const match = firstMatchId ? nodeById.get(firstMatchId) : null;
    if (!viewport || !match) return;
    const offsetX = Math.max(0, (viewport.clientWidth - layout.width * zoom) / 2);
    const offsetY = Math.max(0, (viewport.clientHeight - layout.height * zoom) / 2);
    viewport.scrollLeft = Math.max(0, offsetX + (match.x + NODE_WIDTH / 2) * zoom - viewport.clientWidth / 2);
    viewport.scrollTop = Math.max(0, offsetY + (match.y + NODE_HEIGHT / 2) * zoom - viewport.clientHeight / 2);
  }, [displayMatches, layout.height, layout.width, nodeById, normalizedQuery, searchRequest, zoom]);

  const commitSearch = () => {
    setQuery(queryInput.trim());
    setSelectedId(null);
    setSearchRequest((value) => value + 1);
  };
  const restoreView = () => {
    setZoom(1);
    setQueryInput('');
    setQuery('');
    setSelectedId(null);
    setExpandedIds(new Set());
    setCenterRequest((value) => value + 1);
  };

  const edgePath = (from: PositionedNode, to: PositionedNode) => {
    const x1 = from.x + NODE_WIDTH, y1 = from.y + NODE_HEIGHT / 2;
    const x2 = to.x, y2 = to.y + NODE_HEIGHT / 2;
    const middle = x1 + (x2 - x1) / 2;
    return `M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`;
  };
  return <section className="ff-project-mindmap" role="dialog" aria-modal="true" aria-label={`${projectTitle}项目思维图谱`}>
    <header className="ff-project-mindmap-toolbar">
      <div><Network aria-hidden="true" /><span><strong>项目思维图谱</strong><small>混合向量检索 · Spec → WorkItem → Agent Spec → Skill / 交付物 / TC / 负责人</small></span></div>
      <form className="ff-project-mindmap-search" onSubmit={(event) => { event.preventDefault(); commitSearch(); }}>
        <Search aria-hidden="true" />
        <input autoFocus value={queryInput} onChange={(event) => setQueryInput(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); commitSearch(); } }}
          placeholder="输入后按回车或点击搜索…" aria-label="搜索项目思维图谱" />
        {normalizedQuery && <small>{identityQuery ? `精准匹配 · ${matches.size}` : personOwnerIds.size ? `人物关联 · ${personOwnerIds.size}` : `VECTOR · ${matches.size} 个匹配`}</small>}
        <button type="submit" aria-label="确认搜索思维图谱"><SearchCheck /></button>
      </form>
      <div className="ff-project-mindmap-tools"><button onClick={() => setZoom((value) => Math.max(0.55, value - 0.1))} aria-label="缩小思维图谱"><Minus /></button><span>{Math.round(zoom * 100)}%</span><button onClick={() => setZoom((value) => Math.min(1.4, value + 0.1))} aria-label="放大思维图谱"><Plus /></button><button onClick={restoreView} aria-label="复原思维图谱"><Crosshair /></button><button onClick={onClose} aria-label="关闭项目思维图谱"><X /></button></div>
    </header>
    <div className={`ff-project-mindmap-body${selected ? ' has-inspector' : ''}`}>
      <div ref={viewportRef} className="ff-project-mindmap-viewport">
        <div className="ff-project-mindmap-stage" style={{ width: layout.width * zoom, height: layout.height * zoom }}>
          <div className="ff-project-mindmap-canvas" style={{ width: layout.width, height: layout.height, transform: `scale(${zoom})` }}>
            <svg width={layout.width} height={layout.height} aria-hidden="true">
              {visibleGraph.edges.map((edge) => {
                const from = nodeById.get(edge.from), to = nodeById.get(edge.to);
                if (!from || !to) return null;
                const highlighted = activeIds.size > 0 && activeIds.has(edge.from) && activeIds.has(edge.to);
                const labelX = (from.x + NODE_WIDTH + to.x) / 2;
                const labelY = (from.y + to.y + NODE_HEIGHT) / 2 - 7;
                return <g key={edge.id}><path d={edgePath(from, to)} className={`is-${edge.kind}${highlighted ? ' is-highlighted' : ''}${activeIds.size && !highlighted ? ' is-muted' : ''}`} />
                  {highlighted && edge.label && <text x={labelX} y={labelY} className={`ff-project-mindmap-edge-label is-${edge.kind}`}>{edge.label}</text>}</g>;
              })}
            </svg>
            {layout.nodes.map((node) => {
              const matched = displayMatches.has(node.id), active = activeIds.has(node.id), muted = activeIds.size > 0 && !active;
              const childIds = childrenByParent.get(node.id) ?? [];
              const expandable = childIds.length > 0 && node.depth >= DEFAULT_VISIBLE_DEPTH;
              const hiddenChildCount = childIds.filter((id) => !visibleIds.has(id)).length;
              const childrenVisible = childIds.length > 0 && hiddenChildCount === 0;
              return <button key={node.id} type="button" className={`ff-project-mindmap-node is-${node.kind}${matched ? ' is-match' : ''}${selectedId === node.id ? ' is-selected' : ''}${muted ? ' is-muted' : ''}`}
                style={{ '--mind-x': `${node.x}px`, '--mind-y': `${node.y}px` } as CSSProperties}
                aria-expanded={expandable ? childrenVisible : undefined}
                onClick={() => {
                  setSelectedId((value) => value === node.id ? null : node.id);
                  if (expandable && !normalizedQuery) setExpandedIds((current) => {
                    const next = new Set(current);
                    if (next.has(node.id)) next.delete(node.id); else next.add(node.id);
                    return next;
                  });
                }}
                onDoubleClick={() => { if (node.workItemId) { onClose(); onOpenWorkItem(node.workItemId); } }}>
                <em>{kindLabels[node.kind]}{matched && normalizedQuery ? ` · ${Math.round((vectorMatches.get(node.id) ?? 0) * 100)}%` : ''}</em><strong>{node.title}</strong><span>{node.subtitle}</span>
                {expandable && <b className="ff-project-mindmap-branch" aria-hidden="true">{childrenVisible ? '−' : `+${hiddenChildCount}`}</b>}
              </button>;
            })}
          </div>
        </div>
      </div>
      {selected && <aside className="ff-project-mindmap-inspector">
        <header><span>{kindLabels[selected.kind]}</span><button onClick={() => setSelectedId(null)} aria-label="关闭节点详情"><X /></button></header>
        <h3>{selected.title}</h3><p>{selected.subtitle}</p><div>{selected.detail || '暂无更多详情'}</div>
        <dl><div><dt>直接关联</dt><dd>{graph.edges.filter((edge) => edge.from === selected.id || edge.to === selected.id).length}</dd></div><div><dt>节点类型</dt><dd>{kindLabels[selected.kind]}</dd></div></dl>
        {selectedTraceNodes.length > 0 && <section className="ff-project-mindmap-trace-links" aria-label="节点追溯关系">
          <h4>追溯关系</h4>
          {([
            ['关联需求', selectedTraceNodes.filter((node) => node.kind === 'requirement')],
            ['关联 WorkItem', selectedTraceNodes.filter((node) => node.kind === 'workitem')],
            ['关联 TC / AC', selectedTraceNodes.filter((node) => node.kind === 'testcase')],
          ] as const).map(([label, related]) => related.length > 0 && <div key={label}><span>{label} · {related.length}</span>
            {related.map((node) => <button key={node.id} type="button" onClick={() => setSelectedId(node.id)}>{node.title}<small>{node.subtitle}</small></button>)}</div>)}
        </section>}
        {selected.workItemId && <button className="ff-project-mindmap-open-item" onClick={() => { onClose(); onOpenWorkItem(selected.workItemId!); }}>打开 WorkItem 详情</button>}
        <small>单击节点查看并展开或收起下一级；双击 WorkItem 可直接打开详情。</small>
      </aside>}
    </div>
    <footer><span><i className="is-tree" />层级关系</span><span><i className="is-trace" />需求追踪</span><span><i className="is-dependency" />任务依赖</span><strong>显示 {visibleGraph.nodes.length} / {graph.nodes.length} 个节点 · {visibleGraph.edges.length} / {graph.edges.length} 条关系</strong></footer>
  </section>;
}
