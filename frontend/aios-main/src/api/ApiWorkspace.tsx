import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  FileText,
  FolderPlus,
  GitFork,
  History,
  LayoutGrid,
  Loader2,
  Menu,
  Plus,
  RefreshCw,
  Send,
  Server,
  Settings,
  ShieldCheck,
  Sparkles,
  Wrench,
  X,
} from 'lucide-react';
import { apiClient } from './client';
import type {
  AgentBackendName,
  AgentBackendOptionDto,
  CommandAction,
  AgentRuntimeDto,
  AgentRuntimeEventDto,
  HealthDto,
  LifecycleModel,
  LifecycleRouteDecisionDto,
  ProjectBriefDto,
  SessionStateDto,
  WorkItemDto,
} from './dto';
import { ApiError, normalizeNetworkError } from './errors';
import { AgentRuntimePanel } from './AgentRuntimePanel';
import { AuditTrail } from './AuditTrail';
import { AgentSpecDetail, type WorkItemPreview } from './AgentSpecDetail';
import { MilestoneTaskMonitor, MilestoneTaskSummary } from './MilestoneTaskMonitor';
import { CopyableWorkItemId } from './CopyableWorkItemId';
import { RootWorkItemDetail } from './RootWorkItemDetail';
import { validWorkItemSummary } from './workItemSummary';
import { getEmployeeOptions } from './employeeDirectory';
import { WorkItemDialog } from './WorkItemDialog';
import { WorkItemFilterControls } from './WorkItemFilterControls';
import { TaskDependencyGraph, type TaskDagProject } from './TaskDependencyGraph';
import { TopLevelGraph } from './TopLevelGraph';
import { LifecycleRouteChooser } from './LifecycleRouteChooser';
import { emptyResources, projectSpec, projectTitle, useWorkspaceProjects, type ResourceBundle } from './useWorkspaceProjects';
import { auditTitle, displayLabel, displayTime, progressDescription } from './presentation';
import { PrdReviewPanel } from './PrdReviewPanel';
import {
  createSession,
  autoRepairCommandJob,
  executeCommand,
  getLifecycleRoutes,
  getAgentBackend,
  getCommandJob,
  getSessionState,
  isCommandJobAccepted,
  listAgentRuntime,
  listAgentRuntimeEvents,
  listAgentBackends,
  recommendLifecycleRoutes,
  selectLifecycleRoute,
  selectAgentBackend,
} from './sessions';
import { streamChat } from './chat';
import { observeCommandJob } from './commandJobs';
import type { CommandJobAcceptedDto, CommandResultDto } from './dto';
import {
  actionPlacement,
  isSkipClarificationIntent,
  normalizeSessionId,
  workItemLane,
  workItemPresentation,
  workItemStatusLabel,
  type WorkItemLane,
} from './workflowUi';
import { defaultWorkItemFilters, filterWorkItems, type WorkItemFilterState } from './workItemFilters';

const actionLabels: Record<CommandAction, string> = {
  message: '提交澄清',
  skip_clarification: '跳过澄清并生成 PRD',
  create_spec: '生成 PRD',
  revise: '生成修订版',
  approve: '人工通过',
  reject: '人工驳回',
  rework: '要求返工',
  publish_review: '发布审核',
  convert_to_work_item: '拆解 WorkItem',
  restore_spec_version: '基于历史版本创建新版',
  start_task: '开始任务',
  complete_task: '标记完成',
  fail_task: '标记失败',
};

/** Compact labels for the per-item buttons on work item cards. */
const workItemActionLabels: Partial<Record<CommandAction, string>> = {
  start_task: '开始',
  complete_task: '完成',
  fail_task: '失败',
};

type WorkspaceTab = 'topLevel' | 'kanban' | 'flow' | 'audit';
type ChatTool = 'newProject' | 'resumeSession' | 'restoreSpec' | null;
type ChatMessage = {
  id: string;
  role: 'user' | 'agent';
  body: string;
  detail?: string;
  createdAt: string;
};

const NEW_CHAT_KEY = '__new__';
const DEFAULT_AGENT_BACKENDS: AgentBackendOptionDto[] = [
  {
    provider: 'codex',
    label: 'Codex CLI',
    model: null,
    available: true,
    configuration_env: null,
  },
];

const runtimeOperationLabels: Record<string, string> = {
  recommend_lifecycle: '评估 SDLC 顶层路线',
  analyze_brief: '分析项目需求完整性',
  generate_spec: '生成项目规格',
  generate_prd_prototype: '生成 PRD 交互原型',
  review_spec: '审核项目规格质量',
  decompose_spec: '按 SDLC 路线拆解任务',
  plan_task: '生成任务实施计划',
  review_breakdown: '审核任务拆解结果',
  rewrite_prd: '根据审核批注修订 PRD',
  restore_spec: '恢复历史项目规格',
};

const STALE_STATE_RECOVERY_MESSAGE =
  '状态已被其他操作更新。页面已刷新，请确认最新状态后重新提交。';

const hierarchyColumns: Array<{ kind: NonNullable<WorkItemDto['kind']>; title: string; subtitle: string }> = [
  { kind: 'ROOT', title: '项目需求 (Root)', subtitle: '需求、PRD 与人工审核' },
  { kind: 'MILESTONE', title: 'SDLC 阶段 (Milestones)', subtitle: '所选生命周期路线的阶段' },
];

type BoardColumn = {
  key: string;
  kind: NonNullable<WorkItemDto['kind']>;
  title: string;
  subtitle: string;
  taskLane?: WorkItemLane;
};

const boardColumns: BoardColumn[] = [
  { key: 'root', kind: 'ROOT', title: '项目需求 (Root)', subtitle: '需求、PRD 与人工审核' },
  { key: 'milestone', kind: 'MILESTONE', title: 'SDLC 阶段 (Milestones)', subtitle: '所选生命周期路线的阶段' },
  { key: 'task-todo', kind: 'TASK', taskLane: 'todo', title: '待开始 (To Do)', subtitle: '已下发、尚未开始的子任务' },
  { key: 'task-in-progress', kind: 'TASK', taskLane: 'in_progress', title: '进行中 (In Progress)', subtitle: '执行、评审或受阻的子任务' },
  { key: 'task-done', kind: 'TASK', taskLane: 'done', title: '已完成 (Done)', subtitle: '后端确认完成的子任务' },
];

function lines(value: string): string[] {
  return value.split('\n').map((item) => item.trim()).filter(Boolean);
}

function errorText(error: unknown): string {
  const normalized = normalizeNetworkError(error);
  return `${normalized.code}：${normalized.message}`;
}

function throwIfObservationCancelled(signal: AbortSignal): void {
  if (!signal.aborted) return;
  throw new ApiError({
    status: 0,
    code: 'REQUEST_ABORTED',
    message: '请求已取消。',
    retryable: false,
  });
}

function assigneeLabel(item: WorkItemDto): string {
  return item.suggested_assignee || item.responsible_role || '待分配 Agent';
}

function assigneeInitial(item: WorkItemDto): string {
  const value = assigneeLabel(item).trim();
  return (value.match(/[A-Za-z]/)?.[0] || value[0] || 'A').toUpperCase();
}

function workItemProgress(item: WorkItemDto, state: SessionStateDto, hasAgentSpec: boolean): number {
  if (item.kind === 'TASK') {
    const lane = workItemLane(item.status);
    return lane === 'done' ? 100 : lane === 'in_progress' ? 55 : 0;
  }
  if (item.kind === 'MILESTONE') return hasAgentSpec ? 100 : state.phase === 'AGENT_SPECS_READY' ? 80 : 35;
  const phaseProgress: Record<string, number> = {
    INTAKE: 10,
    NEED_CLARIFICATION: 24,
    CLARIFICATION: 28,
    SPECIFICATION: 46,
    REVIEW: 64,
    HUMAN_REVIEW: 70,
    REWORK: 62,
    APPROVED: 82,
    DECOMPOSITION: 90,
    CONVERTED: 96,
    AGENT_SPECS_READY: 100,
  };
  return phaseProgress[state.phase] ?? 30;
}

function Field({ label, value, onChange, multiline = false }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
}) {
  const className = 'ff-field-control';
  return (
    <label className="ff-field">
      <span>{label}</span>
      {multiline ? (
        <textarea className={`${className} ff-field-multiline`} value={value} onChange={(event) => onChange(event.target.value)} />
      ) : (
        <input className={className} value={value} onChange={(event) => onChange(event.target.value)} />
      )}
    </label>
  );
}

function StatusBadge({ state }: { state: SessionStateDto }) {
  return (
    <div className="ff-status-row">
      <span className="ff-status-badge ff-status-badge-primary">{displayLabel(state.phase)}</span>
      {state.current_spec_status && <span className="ff-status-badge">{displayLabel(state.current_spec_status)}</span>}
    </div>
  );
}

export function ApiWorkspace() {
  const [health, setHealth] = useState<'checking' | 'ok' | 'error'>('checking');
  const {
    projects, catalog, activeSessionId, state, resources, loading: projectsLoading, loadErrors,
    selectSession, updateSessionState, refreshResources, refreshAllProjects,
  } = useWorkspaceProjects();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState('');
  const [restoreRevision, setRestoreRevision] = useState('');
  const [restoreReason, setRestoreReason] = useState('');
  const [motivation, setMotivation] = useState('');
  const [objective, setObjective] = useState('');
  const [scope, setScope] = useState('');
  const [deliverables, setDeliverables] = useState('');
  const [ownerId, setOwnerId] = useState(
    import.meta.env.VITE_LOCAL_ACTOR_HINT?.trim() || 'owner-1',
  );
  const [resumeSessionId, setResumeSessionId] = useState('');
  const [selectedWorkItemId, setSelectedWorkItemId] = useState<string | null>(null);
  const [workItemPreviews, setWorkItemPreviews] = useState<Record<string, WorkItemPreview>>({});
  const [workflowProgress, setWorkflowProgress] = useState<string | null>(null);
  const [failedCommand, setFailedCommand] = useState<{
    sessionId: string;
    commandId: string;
    code: string;
  } | null>(null);
  const [repairingFailure, setRepairingFailure] = useState(false);
  const [observingDecomposition, setObservingDecomposition] = useState(false);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('kanban');
  const [chatOpen, setChatOpen] = useState(true);
  const [chatTool, setChatTool] = useState<ChatTool>(null);
  const [chatMenuOpen, setChatMenuOpen] = useState(false);
  const [chatDraft, setChatDraft] = useState('');
  const [agentBackends, setAgentBackends] = useState(DEFAULT_AGENT_BACKENDS);
  const [agentBackend, setAgentBackend] = useState<AgentBackendName>('codex');
  const [agentBackendMenuOpen, setAgentBackendMenuOpen] = useState(false);
  const [switchingAgentBackend, setSwitchingAgentBackend] = useState(false);
  const [chatMessages, setChatMessages] = useState<Record<string, ChatMessage[]>>({});
  const [agentRuntime, setAgentRuntime] = useState<AgentRuntimeDto[]>([]);
  const [topLevelRuntimeEvents, setTopLevelRuntimeEvents] = useState<AgentRuntimeEventDto[]>([]);
  const [lifecycleDecision, setLifecycleDecision] = useState<LifecycleRouteDecisionDto | null>(null);
  const [lifecycleLoading, setLifecycleLoading] = useState(false);
  const [kanbanFilters, setKanbanFilters] = useState(defaultWorkItemFilters);
  const [flowFilters, setFlowFilters] = useState(defaultWorkItemFilters);
  const [auditFilters, setAuditFilters] = useState(defaultWorkItemFilters);
  const [clipboardResult, setClipboardResult] = useState<string | null>(null);
  const pendingCommandIds = useRef(new Map<string, string>());
  const commandObservations = useRef(new Map<string, { close: () => void; completion: Promise<SessionStateDto> }>());
  const decompositionLifecycles = useRef(new Set<AbortController>());
  const reconciledDecompositionJobs = useRef(new Set<string>());
  const reconcilingDecompositionJobs = useRef(new Set<string>());
  const chatScrollRef = useRef<HTMLDivElement | null>(null);

  const decompositionJobKey = (sessionId: string) =>
    `firstflight.decomposition-job.${sessionId}`;
  const decompositionObservationKey = (sessionId: string, commandId: string) =>
    `${sessionId}:${commandId}`;

  const appendChatMessage = useCallback((
    sessionId: string | null,
    role: ChatMessage['role'],
    body: string,
    detail?: string,
  ) => {
    const key = sessionId ?? NEW_CHAT_KEY;
    const message: ChatMessage = {
      id: crypto.randomUUID(),
      role,
      body,
      detail,
      createdAt: new Date().toISOString(),
    };
    setChatMessages((previous) => ({
      ...previous,
      [key]: [...(previous[key] ?? []), message],
    }));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      apiClient.request<HealthDto>('/healthz', { signal: controller.signal }),
      listAgentBackends(controller.signal),
    ])
      .then(([, options]) => {
        setHealth('ok');
        setAgentBackends(options.length > 0 ? options : DEFAULT_AGENT_BACKENDS);
      })
      .catch((reason) => {
        if ((reason as ApiError).code !== 'REQUEST_ABORTED') {
          setHealth('error');
          setError(errorText(reason));
        }
      });

    return () => controller.abort();
  }, []);

  useEffect(() => () => {
    decompositionLifecycles.current.forEach((controller) => controller.abort());
    decompositionLifecycles.current.clear();
    commandObservations.current.forEach((observation) => observation.close());
    commandObservations.current.clear();
  }, []);

  useEffect(() => {
    if (!activeSessionId) return undefined;
    const controller = new AbortController();
    getAgentBackend(activeSessionId, controller.signal)
      .then((selection) => setAgentBackend(selection.provider))
      .catch((reason) => {
        if ((reason as ApiError).code !== 'REQUEST_ABORTED') {
          setError(errorText(reason));
        }
      });
    return () => controller.abort();
  }, [activeSessionId]);

  useEffect(() => {
    if (!activeSessionId) {
      setAgentRuntime([]);
      return undefined;
    }
    let disposed = false;
    let timer: number | undefined;
    let controller: AbortController | undefined;

    const refresh = async () => {
      controller = new AbortController();
      try {
        const snapshot = await listAgentRuntime(activeSessionId, controller.signal);
        if (!disposed) setAgentRuntime(snapshot);
      } catch {
        // Keep the last successful snapshot. The main error surface owns API failures.
      } finally {
        if (!disposed) timer = window.setTimeout(refresh, 2_000);
      }
    };

    void refresh();
    return () => {
      disposed = true;
      controller?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeSessionId]);

  const activeRuntime = useMemo(() => {
    const running = agentRuntime.find((agent) => agent.status === 'running');
    return running ?? agentRuntime.at(-1) ?? null;
  }, [agentRuntime]);
  const runtimeAgentIds = agentRuntime.map((agent) => agent.agent_session_id).sort().join(',');
  useEffect(() => {
    if (activeTab !== 'topLevel' || !activeSessionId || !runtimeAgentIds) {
      setTopLevelRuntimeEvents([]);
      return undefined;
    }
    let disposed = false;
    let timer: number | undefined;
    const controllers = new Set<AbortController>();
    const refresh = async () => {
      const settled = await Promise.allSettled(agentRuntime.map(async (agent) => {
        const controller = new AbortController();
        controllers.add(controller);
        try {
          return await listAgentRuntimeEvents(activeSessionId, agent.agent_session_id, controller.signal);
        } finally {
          controllers.delete(controller);
        }
      }));
      if (!disposed) {
        const events = settled.flatMap((result) => result.status === 'fulfilled' ? result.value : []);
        const unique = new Map(events.map((event) => [`${event.agent_session_id}:${event.id}`, event]));
        setTopLevelRuntimeEvents([...unique.values()].sort((left, right) => left.created_at.localeCompare(right.created_at)));
        timer = window.setTimeout(refresh, 3_000);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      controllers.forEach((controller) => controller.abort());
    };
  }, [activeSessionId, activeTab, runtimeAgentIds]);
  const visibleChatMessages = chatMessages[activeSessionId ?? NEW_CHAT_KEY] ?? [];
  const selectedAgentBackend = agentBackends.find(
    (option) => option.provider === agentBackend,
  ) ?? DEFAULT_AGENT_BACKENDS[0];

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const scroll = chatScrollRef.current;
      if (scroll) scroll.scrollTop = scroll.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [
    activeSessionId,
    activeRuntime?.current_summary,
    busy,
    chatTool,
    state?.phase,
    visibleChatMessages.length,
    workflowProgress,
  ]);

  const projectList = useMemo(() => Object.values(projects), [projects]);
  const runtimeProjects = useMemo(() => projectList.map((project) => ({
    sessionId: project.state.session_id,
    title: projectTitle(project),
    workItems: project.resources.workItems,
    agentSpecs: project.resources.agentSpecs,
  })), [projectList]);
  const allResources = useMemo<ResourceBundle>(() => ({
    specs: projectList.flatMap((project) => project.resources.specs),
    workItems: projectList.flatMap((project) => project.resources.workItems),
    agentSpecs: projectList.flatMap((project) => project.resources.agentSpecs),
    events: projectList.flatMap((project) => project.resources.events),
  }), [projectList]);
  const workItemProjects = useMemo(() => new Map(projectList.flatMap((project) =>
    project.resources.workItems.map((item) => [item.id, project] as const))), [projectList]);
  const currentProject = activeSessionId ? projects[activeSessionId] : undefined;
  const currentSpec = projectSpec(currentProject);
  useEffect(() => {
    if (!state) {
      setLifecycleDecision(null);
      setLifecycleLoading(false);
      return undefined;
    }
    let disposed = false;
    const controller = new AbortController();
    setLifecycleDecision(null);
    const load = async () => {
      setLifecycleLoading(true);
      try {
        let decision = await getLifecycleRoutes(state.session_id, controller.signal);
        if (
          decision === null
          && state.phase === 'SPECIFICATION'
          && state.current_spec_version_id === null
        ) {
          setWorkflowProgress('澄清已完成，正在依据 SDLC 规则评估推荐路线…');
          decision = await recommendLifecycleRoutes(state.session_id, controller.signal);
        }
        if (!disposed) setLifecycleDecision(decision);
      } catch (reason) {
        const normalized = normalizeNetworkError(reason);
        if (!disposed && normalized.code !== 'REQUEST_ABORTED') setError(errorText(reason));
      } finally {
        if (!disposed) {
          setLifecycleLoading(false);
          setWorkflowProgress(null);
        }
      }
    };
    void load();
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [state?.session_id, state?.phase, state?.current_spec_version_id]);
  const rootWorkItem = useMemo(
    () => resources.workItems.find((item) => item.kind === 'ROOT'),
    [resources.workItems],
  );
  const selectedWorkItem = useMemo(
    () => allResources.workItems.find((item) => item.id === selectedWorkItemId) ?? null,
    [allResources.workItems, selectedWorkItemId],
  );
  const selectedProject = selectedWorkItemId ? workItemProjects.get(selectedWorkItemId) : undefined;
  const selectedResources = selectedProject?.resources ?? emptyResources;
  const selectedSpec = projectSpec(selectedProject);
  const selectedAgentSpecs = useMemo(
    () => selectedResources.agentSpecs.filter((item) => item.work_item_id === selectedWorkItemId),
    [selectedResources.agentSpecs, selectedWorkItemId],
  );
  const employees = useMemo(
    () => getEmployeeOptions(allResources.workItems, allResources.agentSpecs),
    [allResources.workItems, allResources.agentSpecs],
  );
  const previewKey = (workItemId: string) => JSON.stringify([workItemProjects.get(workItemId)?.state.session_id, workItemId]);
  const displayWorkItems = useMemo(() => allResources.workItems.map((item) => {
    const projectId = workItemProjects.get(item.id)?.state.session_id;
    const assigneeId = workItemPreviews[JSON.stringify([projectId, item.id])]?.assigneeId;
    if (item.kind === 'ROOT' || assigneeId === undefined) return item;
    return { ...item, suggested_assignee: assigneeId === '' ? '未指派' : employees.find((employee) => employee.id === assigneeId)?.name || assigneeId };
  }), [allResources.workItems, employees, workItemPreviews, workItemProjects]);
  const placedActions = useMemo(
    () => actionPlacement(state?.legal_actions ?? []),
    [state?.legal_actions],
  );
  const sidebarActions = [
    ...(['message', 'skip_clarification'] as const).filter((action) => placedActions.sidebar.includes(action)),
    ...placedActions.sidebar.filter((action) => !['message', 'skip_clarification', 'restore_spec_version'].includes(action)),
  ];

  const historicalSpecs = resources.specs.filter((spec) => spec.id !== state?.current_spec_version_id);
  const availableAgents = useMemo(() => Array.from(new Set(displayWorkItems.map(assigneeLabel))).sort(), [displayWorkItems]);
  const filterRoots = useMemo(() => projectList.flatMap((project) => {
    const root = project.resources.workItems.find((item) => item.kind === 'ROOT');
    const name = validWorkItemSummary(root?.summary) || root?.title || projectTitle(project);
    return root ? [{ id: root.id, title: `${name}（${root.id}）` }] : [];
  }), [projectList]);
  const rootIdByItem = useMemo(() => new Map([...workItemProjects].map(([itemId, project]) => [
    itemId,
    project.resources.workItems.find((item) => item.kind === 'ROOT')?.id ?? '',
  ])), [workItemProjects]);
  const kanbanVisibleWorkItems = useMemo(
    () => filterWorkItems(displayWorkItems, kanbanFilters, rootIdByItem, assigneeLabel),
    [displayWorkItems, kanbanFilters, rootIdByItem],
  );
  const flowVisibleWorkItems = useMemo(
    () => filterWorkItems(displayWorkItems, flowFilters, rootIdByItem, assigneeLabel),
    [displayWorkItems, flowFilters, rootIdByItem],
  );
  const auditVisibleWorkItems = useMemo(
    () => filterWorkItems(displayWorkItems, auditFilters, rootIdByItem, assigneeLabel),
    [auditFilters, displayWorkItems, rootIdByItem],
  );
  const auditProjectIds = useMemo(() => new Set(
    auditVisibleWorkItems
      .map((item) => workItemProjects.get(item.id)?.state.session_id)
      .filter((sessionId): sessionId is string => Boolean(sessionId)),
  ), [auditVisibleWorkItems, workItemProjects]);
  const auditRuntimeProjects = useMemo(() => runtimeProjects
    .filter((project) => auditProjectIds.has(project.sessionId))
    .map((project) => {
      const visibleIds = new Set(auditVisibleWorkItems
        .filter((item) => workItemProjects.get(item.id)?.state.session_id === project.sessionId)
        .map((item) => item.id));
      return {
        ...project,
        workItems: project.workItems.filter((item) => visibleIds.has(item.id)),
        agentSpecs: project.agentSpecs.filter((item) => visibleIds.has(item.work_item_id)),
      };
    }), [auditProjectIds, auditVisibleWorkItems, runtimeProjects, workItemProjects]);
  const auditEvents = useMemo(() => projectList
    .filter((project) => auditProjectIds.has(project.state.session_id))
    .flatMap((project) => {
      const visibleIds = new Set(auditVisibleWorkItems
        .filter((item) => workItemProjects.get(item.id)?.state.session_id === project.state.session_id)
        .map((item) => item.id));
      const rootId = project.resources.workItems.find((item) => item.kind === 'ROOT')?.id;
      const workItemIds = new Set(project.resources.workItems.map((item) => item.id));
      const workItemByAgentSpec = new Map(project.resources.agentSpecs
        .map((spec) => [spec.id, spec.work_item_id] as const));
      return project.resources.events.filter((event) => {
        const directId = typeof event.payload.work_item_id === 'string'
          && workItemIds.has(event.payload.work_item_id)
          ? event.payload.work_item_id
          : null;
        const agentSpecId = typeof event.payload.agent_spec_id === 'string'
          ? event.payload.agent_spec_id
          : null;
        const relatedId = directId
          ?? (agentSpecId ? workItemByAgentSpec.get(agentSpecId) : undefined)
          ?? rootId;
        return relatedId ? visibleIds.has(relatedId) : false;
      });
    }), [auditProjectIds, auditVisibleWorkItems, projectList, workItemProjects]);
  const auditSpecs = useMemo(() => projectList
    .filter((project) => auditProjectIds.has(project.state.session_id))
    .flatMap((project) => project.resources.specs), [auditProjectIds, projectList]);

  useEffect(() => {
    const keepValidAgent = (current: WorkItemFilterState) => current.agent === 'ALL' || availableAgents.includes(current.agent)
      ? current : { ...current, agent: 'ALL' };
    setKanbanFilters(keepValidAgent);
    setFlowFilters(keepValidAgent);
    setAuditFilters(keepValidAgent);
  }, [availableAgents]);
  const taskDagProjects = useMemo<TaskDagProject[]>(() => projectList.map((project) => ({
    id: project.state.session_id,
    title: projectTitle(project),
    tasks: flowVisibleWorkItems.filter((item) =>
      item.kind === 'TASK'
      && workItemProjects.get(item.id)?.state.session_id === project.state.session_id),
  })).filter((project) => project.tasks.length > 0), [flowVisibleWorkItems, projectList, workItemProjects]);

  function downloadCurrentPrd() {
    if (!currentSpec) return;
    const blob = new Blob([currentSpec.markdown], { type: 'text/markdown;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = `firstFlight-PRD-v${currentSpec.revision}.md`;
    link.click();
    URL.revokeObjectURL(href);
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (busy || projectsLoading) return;
    setBusy(true);
    setError(null);
    const brief: ProjectBriefDto = {
      motivation,
      final_objective: objective,
      known_scope: lines(scope),
      exclusions: [],
      reference_materials: [],
      expected_deliverables: lines(deliverables),
      time_constraints: '第一阶段未指定',
      staffing_constraints: '第一阶段未指定',
      final_approver: ownerId,
      project_manager_ids: [ownerId],
      root_owner_ids: [ownerId],
    };
    setWorkflowProgress('正在读取项目简报并判断是否需要澄清…');
    try {
      const created = await createSession(
        crypto.randomUUID(), brief, undefined, agentBackend,
      );
      appendChatMessage(
        created.session_id,
        'user',
        objective.trim() || motivation.trim() || '创建新项目',
        [motivation.trim(), ...lines(scope), ...lines(deliverables)].filter(Boolean).join(' · '),
      );
      updateSessionState(created);
      selectSession(created.session_id);
      setChatTool(null);
      setMotivation('');
      setObjective('');
      setScope('');
      setDeliverables('');
      await refreshResources(created.session_id);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorkflowProgress(null);
      setBusy(false);
    }
  }

  async function resumeExistingSession() {
    setBusy(true);
    setError(null);
    try {
      const sessionId = normalizeSessionId(resumeSessionId);
      const refreshed = await refreshResources(sessionId);
      selectSession(sessionId);
      setChatTool(null);
      setChatMenuOpen(false);
      setResumeSessionId('');
      const root = refreshed.resources.workItems.find((item) => item.kind === 'ROOT');
      if (root) setSelectedWorkItemId(root.id);
    } catch (reason) {
      setError(reason instanceof Error && reason.message.includes('Session ID')
        ? reason.message
        : errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitCommand(
    baseState: SessionStateDto,
    action: CommandAction,
    message?: string,
    payload: Record<string, unknown> = {},
  ) {
    const fingerprint = JSON.stringify({
      sessionId: baseState.session_id,
      action,
      stateVersion: baseState.state_version,
      message,
      payload,
    });
    const commandId = pendingCommandIds.current.get(fingerprint) ?? crypto.randomUUID();
    pendingCommandIds.current.set(fingerprint, commandId);
    const result = await executeCommand(baseState.session_id, {
      commandId,
      action,
      expectedStateVersion: baseState.state_version,
      message,
      payload,
    });
    if (isCommandJobAccepted(result)) {
      throw new Error('后台拆分任务已开始，当前工作区尚未接入进度观察。');
    }
    pendingCommandIds.current.delete(fingerprint);
    updateSessionState(result.state);
    return result.state;
  }

  async function reconcileDecompositionSuccess(
    result: CommandResultDto,
    signal: AbortSignal,
  ) {
    const storageKey = decompositionJobKey(result.state.session_id);
    const reconciliationKey = `${storageKey}:${result.command_id}`;
    if (reconciledDecompositionJobs.current.has(reconciliationKey)) return result.state;
    if (reconcilingDecompositionJobs.current.has(reconciliationKey)) return result.state;
    reconcilingDecompositionJobs.current.add(reconciliationKey);
    try {
      throwIfObservationCancelled(signal);
      updateSessionState(result.state);
      const refreshed = await refreshResources(result.state.session_id, signal);
      throwIfObservationCancelled(signal);
      const firstTask = refreshed.resources.workItems.find((item) => item.kind === 'TASK');
      if (firstTask) setSelectedWorkItemId(firstTask.id);
      localStorage.removeItem(storageKey);
      setFailedCommand(null);
      reconciledDecompositionJobs.current.add(reconciliationKey);
      return result.state;
    } finally {
      reconcilingDecompositionJobs.current.delete(reconciliationKey);
    }
  }

  async function observeDecomposition(
    sessionId: string,
    accepted: CommandJobAcceptedDto,
    pollImmediately = false,
  ) {
    const observationKey = decompositionObservationKey(sessionId, accepted.command_id);
    const existing = commandObservations.current.get(observationKey);
    if (existing) return existing.completion;
    const lifecycle = new AbortController();
    decompositionLifecycles.current.add(lifecycle);
    const observer = observeCommandJob(sessionId, accepted, {
      pollImmediately,
      onStatus: (snapshot) => {
        if (!lifecycle.signal.aborted) {
          setError(null);
          const message = snapshot.progress_message?.trim();
          if (message) setWorkflowProgress(message);
        }
      },
      onTransportError: (reason) => {
        if (!lifecycle.signal.aborted) setError(errorText(reason));
      },
    });
    const close = () => {
      lifecycle.abort();
      observer.close();
    };
    setObservingDecomposition(true);
    const completion = (async () => {
      try {
        const result = await observer.completion;
        throwIfObservationCancelled(lifecycle.signal);
        return await reconcileDecompositionSuccess(result, lifecycle.signal);
      } catch (reason) {
        throwIfObservationCancelled(lifecycle.signal);
        const normalized = normalizeNetworkError(reason);
        const storageKey = decompositionJobKey(sessionId);
        // A failed background command is terminal. Keeping its id makes every
        // later click resume the same failed job instead of submitting a fresh
        // command, so clear it before surfacing the error. Transport failures
        // do not reject observeCommandJob and remain resumable through storage.
        if (localStorage.getItem(storageKey) === accepted.command_id) {
          localStorage.removeItem(storageKey);
        }
        pendingCommandIds.current.clear();
        if (normalized.code !== 'STALE_STATE') {
          setFailedCommand({
            sessionId,
            commandId: accepted.command_id,
            code: normalized.code,
          });
        }
        if (normalized.status !== 409 || normalized.code !== 'STALE_STATE') throw reason;

        if (commandObservations.current.get(observationKey)?.close === close) {
          commandObservations.current.delete(observationKey);
          if (commandObservations.current.size === 0) setObservingDecomposition(false);
        }
        await refreshResources(sessionId, lifecycle.signal).catch(() => {
          throwIfObservationCancelled(lifecycle.signal);
        });
        throwIfObservationCancelled(lifecycle.signal);
        throw new ApiError({
          status: 409,
          code: 'STALE_STATE',
          message: STALE_STATE_RECOVERY_MESSAGE,
          retryable: true,
        });
      }
    })().finally(() => {
      decompositionLifecycles.current.delete(lifecycle);
      if (commandObservations.current.get(observationKey)?.close === close) {
        commandObservations.current.delete(observationKey);
        if (commandObservations.current.size === 0) {
          setObservingDecomposition(false);
        }
      }
    });
    commandObservations.current.set(observationKey, { close, completion });
    return completion;
  }

  async function submitDecomposition(
    baseState: SessionStateDto,
    onAccepted?: () => void,
  ) {
    const fingerprint = JSON.stringify({
      sessionId: baseState.session_id,
      action: 'convert_to_work_item',
      stateVersion: baseState.state_version,
    });
    const storageKey = decompositionJobKey(baseState.session_id);
    let savedCommandId = localStorage.getItem(storageKey);
    if (savedCommandId && !pendingCommandIds.current.has(fingerprint)) {
      try {
        const savedJob = await getCommandJob(baseState.session_id, savedCommandId);
        if (savedJob.status === 'failed') {
          localStorage.removeItem(storageKey);
          savedCommandId = null;
        }
      } catch {
        // Keep the durable id when its status cannot be checked; the observer
        // can resume it once the backend connection recovers.
      }
    }
    const commandId = pendingCommandIds.current.get(fingerprint)
      ?? savedCommandId
      ?? crypto.randomUUID();
    const existing = commandObservations.current.get(
      decompositionObservationKey(baseState.session_id, commandId),
    );
    if (existing) {
      onAccepted?.();
      return existing.completion;
    }
    pendingCommandIds.current.set(fingerprint, commandId);
    const submission = await executeCommand(baseState.session_id, {
      commandId,
      action: 'convert_to_work_item',
      expectedStateVersion: baseState.state_version,
    });
    if (!isCommandJobAccepted(submission)) {
      throw new Error('Decomposition command did not return a background job.');
    }
    localStorage.setItem(decompositionJobKey(baseState.session_id), submission.command_id);
    setWorkflowProgress('PRD 已确认，正在后台拆解子 WorkItem 和 Agent Spec…');
    onAccepted?.();
    try {
      return await observeDecomposition(baseState.session_id, submission);
    } finally {
      pendingCommandIds.current.delete(fingerprint);
    }
  }

  useEffect(() => {
    if (!state) return;
    const commandId = localStorage.getItem(decompositionJobKey(state.session_id));
    if (!commandId) return;
    const sessionId = state.session_id;
    const resume = async () => {
      try {
        setWorkflowProgress('正在恢复后台任务拆分进度…');
        await observeDecomposition(sessionId, {
          command_id: commandId,
          status: 'processing',
          status_url: `/sessions/${sessionId}/commands/${commandId}`,
          events_url: `/sessions/${sessionId}/commands/${commandId}/events`,
        }, true);
      } catch (reason) {
        const normalized = normalizeNetworkError(reason);
        if (normalized.code === 'REQUEST_ABORTED') return;
        if (state.phase === 'AGENT_SPECS_READY') {
          if (localStorage.getItem(decompositionJobKey(sessionId)) === commandId) {
            localStorage.removeItem(decompositionJobKey(sessionId));
          }
          setError(null);
          return;
        }
        setError(errorText(reason));
      } finally {
        setWorkflowProgress(null);
      }
    };
    void resume();
    return () => {
      commandObservations.current.forEach((observation) => observation.close());
      commandObservations.current.clear();
    };
  }, [state?.session_id]);

  async function runAction(action: CommandAction, inputMessage?: string) {
    if (!state || busy) return;
    const sourceMessage = inputMessage ?? answer;
    const effectiveAction = action === 'message'
      && state.phase === 'NEED_CLARIFICATION'
      && !state.current_spec_version_id
      && state.legal_actions.includes('skip_clarification')
      && isSkipClarificationIntent(sourceMessage)
      ? 'skip_clarification'
      : action;
    const message = effectiveAction === 'message'
      ? sourceMessage
      : effectiveAction === 'rework'
        ? sourceMessage
      : effectiveAction === 'restore_spec_version'
        ? restoreReason
        : undefined;
    if (effectiveAction === 'message' && !message?.trim()) {
      setError('请先填写澄清答案。');
      return;
    }
    if (effectiveAction === 'rework' && !message?.trim()) {
      setError('要求返工时必须填写审核意见。');
      return;
    }
    const sourceRevision = Number(restoreRevision);
    if (
      effectiveAction === 'restore_spec_version'
      && (!Number.isInteger(sourceRevision) || sourceRevision <= 0 || !message?.trim())
    ) {
      setError('请选择历史版本并填写创建新版的原因。');
      return;
    }
    setBusy(true);
    setError(null);
    setWorkflowProgress(
      effectiveAction === 'message'
        ? 'Agent 正在理解你的回复并重新判断当前阶段…'
        : `正在执行：${actionLabels[effectiveAction]}…`,
    );
    try {
      const payload =
        effectiveAction === 'rework'
          ? { comments: message }
          : effectiveAction === 'restore_spec_version'
            ? { source_revision: sourceRevision }
            : {};
      if (effectiveAction === 'skip_clarification') {
        setWorkflowProgress('正在记录跳过澄清，由 Agent 接管模糊决策…');
      }
      let nextState = await submitCommand(state, effectiveAction, message, payload);
      if (effectiveAction === 'skip_clarification') {
        if (nextState.legal_actions.includes('create_spec')) {
          setWorkflowProgress('澄清已结束，正在准备 SDLC 顶层路线选择…');
        }
      }
      setAnswer('');
      setRestoreRevision('');
      setRestoreReason('');
      await refreshResources(state.session_id);
    } catch (reason) {
      const normalized = normalizeNetworkError(reason);
      if (normalized.status === 409 && normalized.code === 'STALE_STATE') {
        pendingCommandIds.current.clear();
        await refreshResources(state.session_id).catch(() => undefined);
        setError(STALE_STATE_RECOVERY_MESSAGE);
      } else if (normalized.code === 'REQUEST_TIMEOUT') {
        const refreshed = await refreshResources(state.session_id).catch(() => null);
        if (refreshed && refreshed.state.state_version !== state.state_version) {
          setError(null);
        } else {
          setError('REQUEST_TIMEOUT：后台可能仍在执行。请稍后刷新页面，系统会从后端恢复最新状态。');
        }
      } else {
        setError(errorText(reason));
      }
    } finally {
      setWorkflowProgress(null);
      setBusy(false);
    }
  }

  async function runWorkItemAction(item: WorkItemDto, action: CommandAction) {
    const targetState = workItemProjects.get(item.id)?.state;
    if (!targetState || busy) return;
    setBusy(true);
    setError(null);
    try {
      // The backend already decided which actions this item may take and
      // shipped them as item.available_actions. This only forwards that
      // decision back as a command; the UI never derives workflow rules.
      await submitCommand(targetState, action, undefined, { work_item_id: item.id });
      await refreshResources(targetState.session_id);
    } catch (reason) {
      const normalized = normalizeNetworkError(reason);
      if (normalized.status === 409 && normalized.code === 'STALE_STATE') {
        pendingCommandIds.current.clear();
        await refreshResources(targetState.session_id).catch(() => undefined);
        setError(STALE_STATE_RECOVERY_MESSAGE);
      } else {
        setError(errorText(reason));
      }
    } finally {
      setBusy(false);
    }
  }

  async function confirmPrdAndDecompose(reviewNote: string) {
    const targetState = selectedProject?.state;
    if (!targetState || busy) return;
    setBusy(true);
    setError(null);
    let nextState = targetState;
    let decompositionRecoveryOwned = false;
    try {
      if (nextState.legal_actions.includes('approve')) {
        setWorkflowProgress('正在确认当前 PRD…');
        nextState = await submitCommand(nextState, 'approve', reviewNote || undefined);
      }
      if (nextState.legal_actions.includes('convert_to_work_item')) {
        nextState = await submitDecomposition(nextState, () => {
          decompositionRecoveryOwned = true;
        });
      }
      if (!decompositionRecoveryOwned) {
        const refreshed = await refreshResources(nextState.session_id);
        const firstTask = refreshed.resources.workItems.find((item) => item.kind === 'TASK');
        if (firstTask) setSelectedWorkItemId(firstTask.id);
      }
    } catch (reason) {
      const normalized = normalizeNetworkError(reason);
      if (normalized.code === 'REQUEST_ABORTED') {
        return;
      } else if (normalized.status === 409 && normalized.code === 'STALE_STATE') {
        if (!decompositionRecoveryOwned) {
          pendingCommandIds.current.clear();
          await refreshResources(targetState.session_id).catch(() => undefined);
        }
        setError(STALE_STATE_RECOVERY_MESSAGE);
        return;
      } else {
        setError(errorText(reason));
      }
      throw reason;
    } finally {
      setWorkflowProgress(null);
      setBusy(false);
    }
  }

  async function manualRefresh() {
    if (!state) return;
    setBusy(true);
    setError(null);
    try {
      await refreshResources(state.session_id);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function autoRepairFailure() {
    if (!failedCommand || repairingFailure) return;
    setRepairingFailure(true);
    setError(null);
    setWorkflowProgress('正在收集数据库上下文、失败调用和常用修复策略…');
    try {
      const accepted = await autoRepairCommandJob(
        failedCommand.sessionId,
        failedCommand.commandId,
        ownerId,
      );
      localStorage.setItem(
        decompositionJobKey(failedCommand.sessionId),
        accepted.command_id,
      );
      setFailedCommand(null);
      await observeDecomposition(failedCommand.sessionId, accepted, true);
    } catch (reason) {
      const normalized = normalizeNetworkError(reason);
      if (normalized.code !== 'REQUEST_ABORTED') setError(errorText(reason));
    } finally {
      setRepairingFailure(false);
      setWorkflowProgress(null);
    }
  }

  async function continueDecomposition() {
    if (!state || busy) return;
    setBusy(true);
    setError(null);
    setWorkflowProgress('正在选择 SDLC 路线并按阶段拆解任务…');
    let decompositionRecoveryOwned = false;
    try {
      await submitDecomposition(state, () => {
        decompositionRecoveryOwned = true;
      });
    } catch (reason) {
      const normalized = normalizeNetworkError(reason);
      if (normalized.code === 'REQUEST_ABORTED') return;
      if (normalized.status === 409 && normalized.code === 'STALE_STATE') {
        if (!decompositionRecoveryOwned) {
          pendingCommandIds.current.clear();
          await refreshResources(state.session_id).catch(() => undefined);
        }
        setError(STALE_STATE_RECOVERY_MESSAGE);
        return;
      }
      setError(errorText(reason));
    } finally {
      setWorkflowProgress(null);
      setBusy(false);
    }
  }

  async function chooseLifecycleRoute(model: LifecycleModel) {
    if (!state || busy || lifecycleDecision?.selected_model) return;
    setBusy(true);
    setError(null);
    setWorkflowProgress('正在确认 SDLC 顶层路线…');
    try {
      const decision = await selectLifecycleRoute(state.session_id, model);
      setLifecycleDecision(decision);
      setActiveTab('topLevel');
      const refreshedState = await getSessionState(state.session_id);
      updateSessionState(refreshedState);
      appendChatMessage(
        state.session_id,
        'agent',
        `已确认顶层路线：${decision.options.find((option) => option.model === model)?.name ?? model}`,
        '正在基于已确认路线生成 PRD；PRD 审核通过后，完整任务规划将严格按该路线展开。',
      );
      setWorkflowProgress('路线已确认，正在生成 PRD…');
      await submitCommand(refreshedState, 'create_spec');
      await refreshResources(state.session_id);
    } catch (reason) {
      const normalized = normalizeNetworkError(reason);
      if (normalized.status === 409 && normalized.code === 'STALE_STATE') {
        pendingCommandIds.current.clear();
        await refreshResources(state.session_id).catch(() => undefined);
        setError(STALE_STATE_RECOVERY_MESSAGE);
      } else {
        setError(errorText(reason));
      }
    } finally {
      setWorkflowProgress(null);
      setBusy(false);
    }
  }

  async function runGlobalInstruction(message: string) {
    const normalized = message.trim().replace(/[。！!？?，,]/g, '').toLowerCase();
    const navigation: Array<{ phrases: string[]; tab: WorkspaceTab; reply: string }> = [
      { phrases: ['打开顶层图', '查看顶层图', '顶层图'], tab: 'topLevel', reply: '已打开当前任务的 SDLC 顶层图。' },
      { phrases: ['打开看板', '查看看板', '任务看板', '看板'], tab: 'kanban', reply: '已打开任务看板。' },
      { phrases: ['打开流转图', '查看流转图', '任务流转图'], tab: 'flow', reply: '已打开任务依赖流转图。' },
      { phrases: ['打开审计记录', '查看审计记录', '审计记录'], tab: 'audit', reply: '已打开审计记录。' },
    ];
    const destination = navigation.find((item) => item.phrases.includes(normalized));
    if (destination) {
      setActiveTab(destination.tab);
      appendChatMessage(state?.session_id ?? null, 'agent', destination.reply);
      return;
    }
    if (['刷新', '刷新状态', '刷新项目'].includes(normalized) && state) {
      await manualRefresh();
      appendChatMessage(state.session_id, 'agent', '已从后端刷新当前项目状态。');
      return;
    }

    if (!state) {
      let createdSessionId: string | null = null;
      setBusy(true);
      setError(null);
      setWorkflowProgress('正在理解项目目标并分析需求完整性…');
      try {
        const responseSessionId = await streamChat(message, (event) => {
          if (event.event === 'session.created' && typeof event.data.session_id === 'string') {
            const created = event.data as unknown as SessionStateDto;
            createdSessionId = created.session_id;
            updateSessionState(created);
            selectSession(created.session_id);
            appendChatMessage(created.session_id, 'user', message);
          }
          if (event.event === 'workflow.error') {
            const errorMessage = typeof event.data.message === 'string'
              ? event.data.message
              : 'Agent 未能完成这次分析。';
            appendChatMessage(createdSessionId, 'agent', '本次分析未完成', errorMessage);
          }
        }, { agentBackend });
        const sessionId = createdSessionId ?? responseSessionId;
        if (sessionId) {
          selectSession(sessionId);
          await refreshResources(sessionId);
        }
      } catch (reason) {
        setError(errorText(reason));
      } finally {
        setWorkflowProgress(null);
        setBusy(false);
      }
      return;
    }

    if (state.legal_actions.includes('message')) {
      await runAction('message', message);
      return;
    }

    const actionAliases: Array<{ action: CommandAction; phrases: string[] }> = [
      { action: 'skip_clarification', phrases: ['跳过澄清', '跳过澄清并生成prd'] },
      { action: 'create_spec', phrases: ['生成prd', '创建prd', '生成项目规格'] },
      { action: 'revise', phrases: ['生成修订版', '修订prd'] },
      { action: 'approve', phrases: ['人工通过', '批准prd', '通过审核'] },
      { action: 'reject', phrases: ['人工驳回', '驳回prd'] },
      { action: 'publish_review', phrases: ['发布审核', '提交审核'] },
      { action: 'convert_to_work_item', phrases: ['拆解workitem', '拆解任务', '继续拆分子任务', '按sdlc拆解'] },
    ];
    const requestedAction = actionAliases.find((item) => item.phrases.includes(normalized))?.action;
    if (
      requestedAction === 'create_spec'
      && state.phase === 'SPECIFICATION'
      && !lifecycleDecision?.selected_model
    ) {
      appendChatMessage(state.session_id, 'agent', '请先从上方两套 SDLC 顶层路线中选择一套，再生成 PRD。');
      return;
    }
    if (requestedAction && state.legal_actions.includes(requestedAction)) {
      if (requestedAction === 'convert_to_work_item') await continueDecomposition();
      else await runAction(requestedAction);
      return;
    }

    const legalLabels = state.legal_actions
      .filter((action) => action !== 'message' && action !== 'restore_spec_version')
      .map((action) => actionLabels[action]);
    appendChatMessage(
      state.session_id,
      'agent',
      `已收到你的全局指令。当前阶段是“${displayLabel(state.phase)}”。`,
      legalLabels.length > 0
        ? `为保持阶段门禁，请明确输入以下操作之一：${legalLabels.join('、')}。也可以输入“打开顶层图”“打开看板”或“刷新状态”。`
        : progressDescription(state),
    );
  }

  async function submitChat(event: FormEvent) {
    event.preventDefault();
    const message = chatDraft.trim();
    if (!message || busy || projectsLoading || health !== 'ok') return;
    appendChatMessage(state?.session_id ?? null, 'user', message);
    setChatDraft('');
    setChatMenuOpen(false);
    setAgentBackendMenuOpen(false);
    await runGlobalInstruction(message);
  }

  async function chooseAgentBackend(provider: AgentBackendName) {
    const option = agentBackends.find((item) => item.provider === provider);
    if (!option?.available || switchingAgentBackend || busy) return;
    setAgentBackendMenuOpen(false);
    if (!state) {
      setAgentBackend(provider);
      return;
    }
    if (provider === agentBackend) return;
    setSwitchingAgentBackend(true);
    setError(null);
    try {
      const selection = await selectAgentBackend(
        state.session_id, provider, ownerId,
      );
      setAgentBackend(selection.provider);
      appendChatMessage(
        state.session_id,
        'agent',
        `模型后端已切换为 ${option.label}`,
        selection.model ? `后续 Agent 调用使用 ${selection.model}` : undefined,
      );
      await refreshResources(state.session_id);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setSwitchingAgentBackend(false);
    }
  }

  const refreshCurrentResources = useCallback(async () => {
    if (selectedProject) await refreshResources(selectedProject.state.session_id);
  }, [refreshResources, selectedProject?.state.session_id]);

  function switchConversation(sessionId: string | null) {
    const wasObservingDecomposition = commandObservations.current.size > 0;
    decompositionLifecycles.current.forEach((controller) => controller.abort());
    decompositionLifecycles.current.clear();
    commandObservations.current.forEach((observation) => observation.close());
    commandObservations.current.clear();
    if (wasObservingDecomposition) {
      setObservingDecomposition(false);
      setBusy(false);
      setWorkflowProgress(null);
    }
    selectSession(sessionId);
    setAnswer('');
    setRestoreRevision('');
    setRestoreReason('');
    setChatTool(null);
    setChatMenuOpen(false);
    setAgentBackendMenuOpen(false);
    setError(null);
  }

  function startNewTask() {
    switchConversation(null);
    setChatTool(null);
    setChatOpen(true);
  }

  return (
    <div className="ff-shell">
      <nav className="ff-activity-bar" aria-label="工作区导航">
        <button className="ff-brand-button" title="firstFlight AI Studio" aria-label="firstFlight AI Studio">
          <Sparkles aria-hidden="true" />
        </button>
        <button
          className={'ff-activity-button ' + (chatOpen ? 'is-active' : '')}
          onClick={() => setChatOpen((open) => !open)}
          title={chatOpen ? '折叠 Agent 对话面板' : '展开 Agent 对话面板'}
          aria-label={chatOpen ? '折叠 Agent 对话面板' : '展开 Agent 对话面板'}
          aria-pressed={chatOpen}
        >
          <Bot aria-hidden="true" />
          <span className="ff-online-dot" />
        </button>
        <div className="ff-activity-divider" />
        <button
          className={'ff-activity-button ' + (activeTab === 'topLevel' ? 'is-active' : '')}
          onClick={() => setActiveTab('topLevel')}
          title="顶层图"
          aria-label="打开顶层图"
          aria-pressed={activeTab === 'topLevel'}
        >
          <GitFork aria-hidden="true" />
        </button>
        <button
          className={'ff-activity-button ' + (activeTab === 'kanban' ? 'is-active' : '')}
          onClick={() => setActiveTab('kanban')}
          title="敏捷任务看板"
          aria-label="打开敏捷任务看板"
          aria-pressed={activeTab === 'kanban'}
        >
          <LayoutGrid aria-hidden="true" />
          {allResources.workItems.length > 0 && <span className="ff-activity-count">{allResources.workItems.length}</span>}
        </button>
        <button
          className={'ff-activity-button ' + (activeTab === 'flow' ? 'is-active' : '')}
          onClick={() => setActiveTab('flow')}
          title="任务流转图"
          aria-label="打开任务流转图"
          aria-pressed={activeTab === 'flow'}
        >
          <GitFork aria-hidden="true" />
        </button>
        <button
          className={'ff-activity-button ' + (activeTab === 'audit' ? 'is-active' : '')}
          onClick={() => setActiveTab('audit')}
          title="审计记录"
          aria-label="打开审计记录"
          aria-pressed={activeTab === 'audit'}
        >
          <ClipboardList aria-hidden="true" />
        </button>
        <div className="ff-activity-spacer" />
        <button className="ff-activity-button" title="系统运行状态" aria-label="系统运行状态">
          <Activity aria-hidden="true" />
        </button>
        <button className="ff-activity-button" title="设置" aria-label="设置">
          <Settings aria-hidden="true" />
        </button>
      </nav>

      {chatOpen && (
        <aside className="ff-agent-panel">
          <header className="ff-agent-header">
            <div className="ff-agent-heading">
              <span className="ff-agent-icon"><Bot aria-hidden="true" /></span>
              <div>
                <div className="ff-agent-title">
                  AI Studio Agent Chat
                  <span className="ff-online-label">ONLINE</span>
                </div>
                <p>Project Orchestration &amp; PRD Engine</p>
              </div>
            </div>
            <span className="ff-agent-select">Project Agent</span>
          </header>

          {catalog.length > 0 && (
            <label className="ff-conversation-picker">
              <span>当前对话</span>
              <select
                aria-label="当前对话项目"
                value={activeSessionId ?? ''}
                disabled={busy && !observingDecomposition}
                onChange={(event) => switchConversation(event.target.value || null)}
              >
                <option value="">新任务</option>
                {catalog.map((project) => (
                  <option key={project.session_id} value={project.session_id} disabled={!projects[project.session_id]}>{project.title}</option>
                ))}
              </select>
            </label>
          )}

          <div className="ff-quick-row">
            <span><Sparkles aria-hidden="true" /> {state ? '全局任务对话' : '新项目需求'}</span>
            <span className={health === 'ok' ? 'is-online' : 'is-offline'}>
              {health === 'ok' ? '后端在线' : health === 'error' ? '后端不可用' : '正在连接'}
            </span>
          </div>

          <div ref={chatScrollRef} className="ff-chat-scroll" role="log" aria-label="Agent 对话与工作记录">
            {visibleChatMessages.length === 0 && !state && (
              <div className="ff-message ff-message-agent">
                <span className="ff-avatar">PM</span>
                <div className="ff-message-content">
                  <p className="ff-message-meta">Project Manager Agent</p>
                  <div className="ff-message-bubble">
                    直接描述你要完成的项目，我会创建任务并分析需求。也可以点击输入框左侧菜单，打开完整项目表单或恢复已有 Session。
                  </div>
                </div>
              </div>
            )}

            {visibleChatMessages.map((message) => (
              <div key={message.id} className={`ff-message ff-message-${message.role}`}>
                {message.role === 'agent' && <span className="ff-avatar">PM</span>}
                <div className="ff-message-content">
                  <p className="ff-message-meta">{message.role === 'agent' ? 'Project Orchestrator Agent' : 'Project Owner'} · {displayTime(message.createdAt)}</p>
                  <div className="ff-message-bubble">
                    <strong>{message.body}</strong>
                    {message.detail && <p>{message.detail}</p>}
                  </div>
                </div>
                {message.role === 'user' && <span className="ff-avatar ff-avatar-user">U</span>}
              </div>
            ))}

            {state && visibleChatMessages.length === 0 && (
              <div className="ff-message ff-message-user">
                <div className="ff-message-content">
                  <p className="ff-message-meta">Project Owner</p>
                  <div className="ff-message-bubble">{rootWorkItem?.title || rootWorkItem?.objective || '当前项目需求'}</div>
                </div>
                <span className="ff-avatar ff-avatar-user">U</span>
              </div>
            )}

            {state && (
              <div className="ff-message ff-message-agent">
                <span className="ff-avatar">PM</span>
                <div className="ff-message-content">
                  <p className="ff-message-meta">Project Orchestrator Agent</p>
                  <div className="ff-message-bubble">
                    <strong>当前阶段：{displayLabel(state.phase)}</strong>
                    <p>{progressDescription(state)}</p>
                    <StatusBadge state={state} />
                    {currentSpec && <p className="ff-inline-note">当前 PRD v{currentSpec.revision}</p>}
                  </div>
                </div>
              </div>
            )}

            {state?.outstanding_questions.map((question) => (
              <div key={question.question_id} className="ff-message ff-message-agent">
                <span className="ff-avatar ff-avatar-warning">?</span>
                <div className="ff-message-content">
                  <p className="ff-message-meta">Clarification Agent</p>
                  <div className="ff-message-bubble ff-question-bubble">
                    <strong>{question.question}</strong>
                    <p>{question.reason}</p>
                  </div>
                </div>
              </div>
            ))}

            {lifecycleDecision && (
              <div className="ff-message ff-message-agent ff-route-message">
                <span className="ff-avatar">PM</span>
                <div className="ff-message-content">
                  <p className="ff-message-meta">SDLC Planning Agent</p>
                  <LifecycleRouteChooser
                    decision={lifecycleDecision}
                    busy={busy}
                    onSelect={(model) => void chooseLifecycleRoute(model)}
                  />
                </div>
              </div>
            )}

            {(busy || lifecycleLoading || observingDecomposition || activeRuntime?.status === 'running') && (
              <div className="ff-message ff-message-agent ff-thinking-message" aria-live="polite">
                <span className="ff-avatar ff-avatar-thinking"><Loader2 className="ff-spin" aria-hidden="true" /></span>
                <div className="ff-message-content">
                  <p className="ff-message-meta">Project Orchestrator Agent · 思考中</p>
                  <div className="ff-message-bubble ff-thinking-bubble">
                    <strong>{workflowProgress || (activeRuntime
                      ? runtimeOperationLabels[activeRuntime.current_operation] ?? activeRuntime.current_operation
                      : '正在理解指令并准备下一步')}</strong>
                    {state && <p>当前阶段：{displayLabel(state.phase)}</p>}
                    {activeRuntime?.current_summary && <p>{activeRuntime.current_summary}</p>}
                    {activeRuntime && (
                      <div className="ff-runtime-meta">
                        <span>操作 {runtimeOperationLabels[activeRuntime.current_operation] ?? activeRuntime.current_operation}</span>
                        <span>调用 {activeRuntime.call_count}</span>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {activeRuntime && activeRuntime.status !== 'running' && !busy && (
              <div className={`ff-agent-event ${activeRuntime.status === 'error' ? 'is-error' : ''}`}>
                <span className="ff-event-check">{activeRuntime.status === 'error' ? <AlertCircle aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}</span>
                <div>
                  <strong>{activeRuntime.status === 'error' ? 'Agent 运行异常' : 'Agent 已完成本阶段处理'}</strong>
                  <p>{runtimeOperationLabels[activeRuntime.current_operation] ?? activeRuntime.current_operation} · {activeRuntime.current_summary || `累计调用 ${activeRuntime.call_count} 次`}</p>
                </div>
              </div>
            )}

            {error && (
              <div className="ff-message ff-message-agent">
                <span className="ff-avatar ff-avatar-warning">!</span>
                <div className="ff-message-content">
                  <p className="ff-message-meta">System Agent</p>
                  <div role="alert" className="ff-message-bubble ff-error-bubble">
                    <span>{error}</span>
                    {failedCommand && (
                      <button
                        type="button"
                        className="ff-auto-repair-button"
                        disabled={repairingFailure}
                        onClick={() => void autoRepairFailure()}
                      >
                        {repairingFailure ? <Loader2 className="ff-spin" aria-hidden="true" /> : <Wrench aria-hidden="true" />}
                        自动修复
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            {state && resources.events.slice(-3).map((event) => (
              <div key={event.id} className="ff-agent-event">
                <span className="ff-event-check"><CheckCircle2 aria-hidden="true" /></span>
                <div><strong>{auditTitle(event)}</strong><p>{displayTime(event.created_at)} · {event.actor_id || '系统 Agent'}</p></div>
              </div>
            ))}

            {chatTool === 'newProject' && (
              <form onSubmit={onCreate} className="ff-form-card ff-chat-tool-card">
                <div className="ff-tool-card-header"><strong>生成新项目</strong><button type="button" onClick={() => setChatTool(null)} aria-label="关闭新项目表单"><X aria-hidden="true" /></button></div>
                <Field label="为什么要做" value={motivation} onChange={setMotivation} multiline />
                <Field label="最终目标" value={objective} onChange={setObjective} multiline />
                <Field label="已知范围（每行一项）" value={scope} onChange={setScope} multiline />
                <Field label="预期交付物（每行一项）" value={deliverables} onChange={setDeliverables} multiline />
                <Field label="负责人标识" value={ownerId} onChange={setOwnerId} />
                <button disabled={busy || projectsLoading || health !== 'ok'} className="ff-primary-button ff-full-button">
                  {busy && <Loader2 className="ff-spin" aria-hidden="true" />} 创建并分析
                </button>
              </form>
            )}

            {chatTool === 'resumeSession' && (
              <section className="ff-form-card ff-chat-tool-card">
                <div className="ff-tool-card-header"><strong>恢复已有项目 Session</strong><button type="button" onClick={() => setChatTool(null)} aria-label="关闭恢复表单"><X aria-hidden="true" /></button></div>
                <p className="ff-tool-card-description">读取后端已有状态，不创建新的 Agent 运行。</p>
                <Field label="Session ID" value={resumeSessionId} onChange={setResumeSessionId} />
                <button type="button" disabled={busy || health !== 'ok'} onClick={resumeExistingSession} className="ff-secondary-button ff-full-button">恢复并打开</button>
              </section>
            )}

            {chatTool === 'restoreSpec' && state && (
              <section className="ff-form-card ff-chat-tool-card">
                <div className="ff-tool-card-header"><strong>从历史 PRD 创建新版</strong><button type="button" onClick={() => setChatTool(null)} aria-label="关闭历史版本表单"><X aria-hidden="true" /></button></div>
                <p className="ff-tool-card-description">复制所选旧版内容并生成新版本，已有记录会保留。</p>
                <select aria-label="历史 PRD 版本" value={restoreRevision} onChange={(event) => setRestoreRevision(event.target.value)} className="ff-field-control">
                  <option value="">选择历史版本</option>
                  {historicalSpecs.map((spec) => <option key={spec.id} value={spec.revision}>v{spec.revision} · {displayLabel(spec.status)}</option>)}
                </select>
                <Field label="创建新版的原因" value={restoreReason} onChange={setRestoreReason} multiline />
                <button type="button" disabled={busy || !restoreRevision || !restoreReason.trim()} onClick={() => runAction('restore_spec_version')} className="ff-secondary-button ff-full-button">基于历史版本创建新版</button>
              </section>
            )}

            {state && currentSpec && (
              <button type="button" aria-label="查看 PRD 与审核意见" onClick={() => rootWorkItem && setSelectedWorkItemId(rootWorkItem.id)} className="ff-related-work-item">
                <FileText aria-hidden="true" />
                <span><strong>查看 PRD 与审核意见</strong><small>v{currentSpec.revision} · {displayLabel(currentSpec.status)}</small></span>
              </button>
            )}

            {state && state.review_findings.length > 0 && (
              <div className="ff-warning-note"><AlertCircle aria-hidden="true" /><span>当前有 {state.review_findings.length} 项审核发现，请在 PRD 审核窗口中处理。</span></div>
            )}
          </div>

          <footer className="ff-chat-composer">
            <form onSubmit={submitChat} className="ff-stream-composer">
              <div className="ff-chat-menu-wrap">
                <button type="button" className={`ff-chat-menu-button ${chatMenuOpen ? 'is-open' : ''}`} onClick={() => setChatMenuOpen((open) => !open)} aria-label="展开常用功能" aria-expanded={chatMenuOpen}>
                  <Menu aria-hidden="true" />
                </button>
                {chatMenuOpen && (
                  <div className="ff-chat-menu" role="menu">
                    <button type="button" role="menuitem" onClick={() => { startNewTask(); setChatTool('newProject'); setChatMenuOpen(false); }}><FolderPlus aria-hidden="true" /><span><strong>生成新项目</strong><small>打开完整项目简报</small></span></button>
                    <button type="button" role="menuitem" onClick={() => { setChatTool('resumeSession'); setChatMenuOpen(false); }}><History aria-hidden="true" /><span><strong>恢复已有 Session</strong><small>读取后端项目状态</small></span></button>
                    {state && <button type="button" role="menuitem" onClick={() => { void manualRefresh(); setChatMenuOpen(false); }}><RefreshCw aria-hidden="true" /><span><strong>刷新当前项目</strong><small>同步阶段与运行状态</small></span></button>}
                    {state?.legal_actions.includes('restore_spec_version') && historicalSpecs.length > 0 && <button type="button" role="menuitem" onClick={() => { setChatTool('restoreSpec'); setChatMenuOpen(false); }}><FileText aria-hidden="true" /><span><strong>恢复历史 PRD</strong><small>基于旧版本创建新版</small></span></button>}
                  </div>
                )}
              </div>
              <textarea
                aria-label="给 Project Agent 发送消息"
                value={chatDraft}
                disabled={busy || health !== 'ok'}
                onChange={(event) => setChatDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder={state ? '输入全局指令、澄清答案或页面命令…' : '直接描述新项目，或从左侧菜单打开完整表单…'}
                rows={1}
              />
              <div className="ff-agent-backend-picker">
                <button
                  type="button"
                  className="ff-agent-backend-trigger"
                  disabled={busy || switchingAgentBackend}
                  aria-label="切换 Agent 模型后端"
                  aria-expanded={agentBackendMenuOpen}
                  onClick={() => {
                    setChatMenuOpen(false);
                    setAgentBackendMenuOpen((open) => !open);
                  }}
                >
                  {switchingAgentBackend
                    ? <Loader2 className="ff-spin" aria-hidden="true" />
                    : <span className={`ff-provider-mark is-${agentBackend}`}>{selectedAgentBackend.label.slice(0, 1)}</span>}
                  <span>{selectedAgentBackend.label}</span>
                  <ChevronDown aria-hidden="true" />
                </button>
                {agentBackendMenuOpen && (
                  <div className="ff-agent-backend-menu" role="menu">
                    <header>
                      <strong>Agent 模型后端</strong>
                      <small>当前项目后续调用统一使用所选来源</small>
                    </header>
                    {agentBackends.map((option) => (
                      <button
                        type="button"
                        role="menuitemradio"
                        aria-checked={option.provider === agentBackend}
                        key={option.provider}
                        disabled={!option.available || busy || switchingAgentBackend}
                        onClick={() => void chooseAgentBackend(option.provider)}
                      >
                        <span className={`ff-provider-mark is-${option.provider}`}>{option.label.slice(0, 1)}</span>
                        <span>
                          <strong>{option.label}</strong>
                          <small>{option.available ? (option.model || '使用本地默认模型') : `未配置 ${option.configuration_env}`}</small>
                        </span>
                        <i>{option.provider === agentBackend ? '当前' : option.available ? '可用' : '未配置'}</i>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button type="submit" className="ff-chat-send-button" disabled={busy || !chatDraft.trim() || health !== 'ok'} aria-label="发送消息">
                {busy ? <Loader2 className="ff-spin" aria-hidden="true" /> : <Send aria-hidden="true" />}
              </button>
            </form>
            {state && (
              <div className="ff-composer-actions ff-suggested-actions">
                {sidebarActions.filter((action) => action !== 'message' && !(
                  action === 'create_spec'
                  && state.phase === 'SPECIFICATION'
                  && !lifecycleDecision?.selected_model
                )).map((action) => (
                  <button type="button" key={action} disabled={busy} onClick={() => runAction(action)} className="ff-secondary-button">{actionLabels[action]}</button>
                ))}
                {state.legal_actions.includes('convert_to_work_item') && <button type="button" disabled={busy || observingDecomposition} onClick={() => void continueDecomposition()} className="ff-secondary-button">继续拆分子任务</button>}
              </div>
            )}
            <div className="ff-composer-foot">
              <span><span className="ff-small-dot" /> 阶段门禁与后端状态驱动</span>
              {state && <button type="button" disabled={busy} onClick={startNewTask}>新任务</button>}
            </div>
          </footer>
        </aside>
      )}

      <main className="ff-workspace">
        <header className="ff-top-tabs">
          <div className="ff-tabs">
            <button className={activeTab === 'topLevel' ? 'is-active' : ''} onClick={() => setActiveTab('topLevel')}>
              <GitFork aria-hidden="true" />
              顶层图
              <span>{lifecycleDecision?.selected_model ? 'SDLC' : '—'}</span>
            </button>
            <button className={activeTab === 'kanban' ? 'is-active' : ''} onClick={() => setActiveTab('kanban')}>
              <LayoutGrid aria-hidden="true" />
              Kanban Board (敏捷看板)
              <span>{allResources.workItems.length}</span>
            </button>
            <button className={activeTab === 'flow' ? 'is-active' : ''} onClick={() => setActiveTab('flow')}>
              <GitFork aria-hidden="true" />
              DAG Flow Map (任务流转图)
            </button>
            <button className={activeTab === 'audit' ? 'is-active' : ''} onClick={() => setActiveTab('audit')}>
              <ClipboardList aria-hidden="true" />
              Audit Trail (审计记录)
              <span>{allResources.events.length}</span>
            </button>
          </div>
          <div className="ff-api-status">
            <Server aria-hidden="true" />
            <span className={health === 'ok' ? 'is-online' : 'is-offline'}>
              {health === 'ok' ? 'Backend Online' : health === 'error' ? 'Backend Offline' : 'Checking API'}
            </span>
          </div>
        </header>

        <section className={activeTab === 'topLevel' ? 'ff-tab-pane is-active' : 'ff-tab-pane'} aria-hidden={activeTab !== 'topLevel'}>
          <div className="ff-top-level-toolbar">
            <div>
              <h2>项目生命周期顶层图</h2>
              <p>根据任务澄清后选择的 SDLC 路线，自上而下展示阶段、门禁和交付物。</p>
            </div>
            <label>
              <span>当前任务</span>
              <select
                aria-label="顶层图项目"
                value={activeSessionId ?? ''}
                onChange={(event) => switchConversation(event.target.value || null)}
              >
                <option value="">请选择任务</option>
                {catalog.map((project) => (
                  <option key={project.session_id} value={project.session_id} disabled={!projects[project.session_id]}>{project.title}</option>
                ))}
              </select>
            </label>
            <button onClick={() => void refreshAllProjects()} disabled={busy || projectsLoading} className="ff-secondary-button">
              <RefreshCw className={projectsLoading ? 'ff-spin' : ''} aria-hidden="true" />刷新
            </button>
          </div>
          <div className="ff-top-level-canvas">
            {currentProject ? (
              <TopLevelGraph
                projectTitle={projectTitle(currentProject)}
                agentSpecs={currentProject.resources.agentSpecs}
                workItems={currentProject.resources.workItems}
                decision={lifecycleDecision}
                runtimes={agentRuntime}
                runtimeEvents={topLevelRuntimeEvents}
                projectPhase={displayLabel(currentProject.state.phase)}
                onOpenWorkItem={setSelectedWorkItemId}
              />
            ) : (
              <div className="ff-top-level-empty" role="status"><GitFork aria-hidden="true" /><strong>请选择一个任务</strong><p>选择后显示该任务的 SDLC 顶层阶段路线。</p></div>
            )}
          </div>
        </section>

        <section className={activeTab === 'kanban' ? 'ff-tab-pane is-active' : 'ff-tab-pane'} aria-hidden={activeTab !== 'kanban'}>
          <div className="ff-board-toolbar ff-filter-toolbar">
            <WorkItemFilterControls
              value={kanbanFilters}
              roots={filterRoots}
              agents={availableAgents}
              searchLabel="搜索工单"
              onChange={setKanbanFilters}
            />
            <div className="ff-toolbar-spacer" />
            {currentSpec && <button onClick={downloadCurrentPrd} className="ff-download-button"><FileText aria-hidden="true" />下载 PRD.md</button>}
            <span className="ff-board-project-count">共 {catalog.length} 个项目</span>
            <button onClick={() => void refreshAllProjects()} disabled={busy || projectsLoading} className="ff-secondary-button"><RefreshCw className={projectsLoading ? 'ff-spin' : ''} aria-hidden="true" />刷新看板</button>
            <button onClick={startNewTask} disabled={busy} className="ff-new-epic-button"><Plus aria-hidden="true" />规划新主工单</button>
          </div>

          {(error || workflowProgress) && (
            <div className="ff-alert-stack">
              {error && <div role="alert" className="ff-page-alert ff-page-alert-error"><AlertCircle aria-hidden="true" /><span>{error}</span>{failedCommand && <button type="button" className="ff-auto-repair-button" disabled={repairingFailure} onClick={() => void autoRepairFailure()}>{repairingFailure ? <Loader2 className="ff-spin" aria-hidden="true" /> : <Wrench aria-hidden="true" />}自动修复</button>}</div>}
              {workflowProgress && <div className="ff-page-alert ff-page-alert-progress"><Loader2 className="ff-spin" aria-hidden="true" /><span>{workflowProgress}</span></div>}
            </div>
          )}
          {clipboardResult && <div className="ff-clipboard-status" role="status" aria-live="polite">{clipboardResult}</div>}

          {Object.entries(loadErrors).map(([sessionId, message]) => (
            <div key={sessionId} role="alert" className="ff-page-alert ff-page-alert-error"><AlertCircle aria-hidden="true" /><span>{message}。可点击“刷新看板”重试。</span></div>
          ))}

          <div className="ff-board-scroll">
            {boardColumns.map((column) => {
              const items = kanbanVisibleWorkItems.filter((item) => item.kind === column.kind
                && (!column.taskLane || workItemLane(item.status) === column.taskLane));
              return (
                <section key={column.key} className={`ff-board-column ${column.taskLane ? `is-task-lane is-${column.taskLane}` : ''}`} aria-label={column.title}>
                  <header>
                    <div>
                      <h2>{column.title}</h2>
                      <p>{column.subtitle}</p>
                    </div>
                    <span>{items.length}</span>
                  </header>
                  <div className="ff-card-list">
                    {items.map((item) => {
                      const project = workItemProjects.get(item.id)!;
                      const hasAgentSpec = project.resources.agentSpecs.some((spec) => spec.work_item_id === item.id);
                      const presentation = workItemPresentation(item.kind, hasAgentSpec);
                      const disabled = presentation.detailKind === 'prd' && !projectSpec(project);
                      const progress = workItemProgress(item, project.state, hasAgentSpec);
                      return (
                        <div
                          key={item.id}
                          className={'ff-work-card ' + (selectedWorkItemId === item.id ? 'is-selected' : '')}
                        >
                          <div className="ff-card-tags">
                            <CopyableWorkItemId id={item.id} onResult={setClipboardResult} />
                            <span className="ff-priority">{item.kind === 'ROOT' ? 'P0' : item.kind === 'MILESTONE' ? 'P1' : 'P2'}</span>
                            {item.kind === 'TASK' && <span className={`ff-card-status is-${workItemLane(item.status)}`}>{workItemStatusLabel(item.status)}</span>}
                            <span className="ff-card-kind">{item.kind === 'ROOT' ? 'Epic' : item.kind === 'MILESTONE' ? 'SDLC Phase' : 'Subtask'}</span>
                          </div>
                          <button
                            type="button"
                            className={`ff-work-card-main${disabled ? ' is-disabled' : ''}`}
                            disabled={disabled}
                            onClick={() => setSelectedWorkItemId(item.id)}
                          >
                            <h3>{item.kind === 'ROOT' ? validWorkItemSummary(item.summary) || item.title || item.id : item.title || item.id}</h3>
                            {projectList.length > 1 && item.kind !== 'ROOT' && <span className="ff-card-project">项目：{projectTitle(project)}</span>}
                            <p>{item.kind === 'ROOT'
                              ? [item.title, item.objective || item.description].filter((value, index, values) => Boolean(value) && values.indexOf(value) === index).join(' · ') || '未提供任务目标'
                              : item.objective || item.description || '未提供任务目标'}</p>
                            {item.parent_id && <div className="ff-parent-link">产生自：#{item.parent_id}</div>}
                            {item.kind === 'MILESTONE' && (
                              <MilestoneTaskSummary
                                milestoneId={item.id}
                                workItems={project.resources.workItems}
                              />
                            )}
                            <div className="ff-card-footer">
                              <span className="ff-assignee"><i>{assigneeInitial(item)}</i>{assigneeLabel(item)}</span>
                              <span className="ff-progress-label">{progress}%</span>
                            </div>
                            <div className="ff-progress-track"><span style={{ width: progress + '%' }} /></div>
                            <span className="ff-card-action">{disabled ? 'PRD 生成后可打开' : presentation.cardLabel}</span>
                          </button>
                          {(item.available_actions ?? []).length > 0 && (
                            <div className="ff-card-exec" onClick={(event) => event.stopPropagation()}>
                              {(item.available_actions ?? []).map((action) => (
                                <button
                                  key={action}
                                  type="button"
                                  disabled={busy}
                                  className={`ff-exec-btn is-${action}`}
                                  onClick={() => runWorkItemAction(item, action as CommandAction)}
                                >
                                  {workItemActionLabels[action as CommandAction] ?? action}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                    {items.length === 0 && <div className="ff-empty-column">当前阶段尚无卡片</div>}
                  </div>
                </section>
              );
            })}
          </div>
        </section>

        <section className={activeTab === 'flow' ? 'ff-tab-pane is-active' : 'ff-tab-pane'} aria-hidden={activeTab !== 'flow'}>
          <div className="ff-flow-toolbar">
            <div><h2>任务依赖流转图</h2><p>根据后端返回的 WorkItem 层级和依赖关系展示，只读，不在浏览器中修改状态。</p></div>
            <span>{flowVisibleWorkItems.length} NODES</span>
          </div>
          <div className="ff-filter-toolbar ff-flow-filter-toolbar">
            <WorkItemFilterControls
              value={flowFilters}
              roots={filterRoots}
              agents={availableAgents}
              searchLabel="搜索流转图工单"
              onChange={setFlowFilters}
            />
          </div>
          <div className="ff-flow-canvas">
            {flowVisibleWorkItems.length === 0 ? <div className="ff-flow-filter-empty" role="status" aria-live="polite">没有符合当前筛选条件的任务</div> : (
              <div className="ff-flow-graph">
                {hierarchyColumns.map((column, columnIndex) => (
                  <div key={column.kind} className="ff-flow-lane">
                    <header><span>{columnIndex + 1}</span>{column.title}</header>
                    {flowVisibleWorkItems.filter((item) => item.kind === column.kind).map((item) => (
                      <button
                        key={item.id}
                        data-wi-id={item.id}
                        onClick={() => setSelectedWorkItemId(item.id)}
                        className="ff-flow-node"
                      >
                        <span className="ff-flow-node-icon">{item.kind === 'ROOT' ? <ShieldCheck aria-hidden="true" /> : <GitFork aria-hidden="true" />}</span>
                        <span><strong>{item.title || item.id}</strong><small>#{item.id} · {assigneeLabel(item)}</small></span>
                        {item.dependency_work_item_ids.length > 0 && <em>依赖 {item.dependency_work_item_ids.length}</em>}
                      </button>
                    ))}
                    {flowVisibleWorkItems.every((item) => item.kind !== column.kind) && (
                      <div className="ff-flow-empty">
                        {displayWorkItems.some((item) => item.kind === column.kind)
                          ? '当前筛选条件已隐藏此类节点'
                          : '等待后端生成'}
                      </div>
                    )}
                  </div>
                ))}
                <section className="ff-flow-task-lane" aria-label="子任务 (Tasks)">
                  <header><span>3</span><div><strong>子任务 (Tasks)</strong><small>按项目与依赖深度排列</small></div></header>
                  <TaskDependencyGraph
                    projects={taskDagProjects}
                    onOpenWorkItem={setSelectedWorkItemId}
                    emptyMessage={displayWorkItems.some((item) => item.kind === 'TASK')
                      ? '当前筛选条件已隐藏此类节点'
                      : '等待后端生成子任务'}
                  />
                </section>
              </div>
            )}
          </div>
        </section>

        <section className={activeTab === 'audit' ? 'ff-tab-pane is-active' : 'ff-tab-pane'} aria-hidden={activeTab !== 'audit'}>
          <div className="ff-filter-toolbar ff-audit-filter-toolbar">
            <WorkItemFilterControls
              value={auditFilters}
              roots={filterRoots}
              agents={availableAgents}
              searchLabel="搜索审计记录关联工单"
              onChange={setAuditFilters}
            />
          </div>
          <div className="ff-audit-scroll">
            {activeTab === 'audit' && error && <div role="alert" className="ff-page-alert ff-page-alert-error"><AlertCircle aria-hidden="true" /><span>{error}</span></div>}
            {auditVisibleWorkItems.length === 0 ? (
              <div className="ff-flow-filter-empty" role="status" aria-live="polite">没有符合当前筛选条件的审计记录</div>
            ) : (
              <>
                {activeTab === 'audit' && <AgentRuntimePanel projects={auditRuntimeProjects} />}
                <AuditTrail events={auditEvents} specs={auditSpecs} />
              </>
            )}
          </div>
        </section>

        <footer className="ff-status-bar">
          <div><span className="ff-small-dot" /> AI STUDIO ENGINE <span>UTF-8</span><span>Backend Contract</span></div>
          <div><span className={health === 'ok' ? 'ff-small-dot' : 'ff-small-dot is-error'} /> AGENT NETWORK {health === 'ok' ? 'ONLINE' : 'OFFLINE'} <span>{allResources.agentSpecs.length} SPECS</span></div>
        </footer>
      </main>

      {selectedWorkItem && (
        <WorkItemDialog
          contentKey={selectedWorkItem.id}
          title={selectedWorkItem.kind === 'ROOT'
            ? '项目需求详情'
            : selectedWorkItem.kind === 'MILESTONE'
              ? 'SDLC 阶段详情'
              : '任务详情'}
          onClose={() => setSelectedWorkItemId(null)}
        >
          {selectedWorkItem.kind === 'ROOT' && (
            <RootWorkItemDetail item={selectedWorkItem} onCopyResult={setClipboardResult}>
              {selectedProject && selectedSpec ? (
                <PrdReviewPanel
                  key={selectedWorkItem.id + ':' + selectedSpec.id}
                  fallbackSpec={selectedSpec}
                  wi={selectedWorkItem.id}
                  sessionState={selectedProject.state}
                  workflowBusy={busy}
                  onConfirmAndDecompose={confirmPrdAndDecompose}
                  onResourcesChanged={refreshCurrentResources}
                />
              ) : (
                <div className="ff-empty-detail">
                  <p>当前任务尚未生成 PRD。</p>
                </div>
              )}
            </RootWorkItemDetail>
          )}
          {selectedWorkItem.kind === 'MILESTONE' && (
            <MilestoneTaskMonitor
              key={selectedWorkItem.id}
              milestone={selectedWorkItem}
              workItems={selectedResources.workItems}
              onOpenWorkItem={setSelectedWorkItemId}
            />
          )}
          {selectedWorkItem.kind === 'TASK' && (
            <AgentSpecDetail
              key={previewKey(selectedWorkItem.id)}
              item={selectedWorkItem}
              agentSpecs={selectedAgentSpecs}
              sourceSpecs={selectedResources.specs}
              employees={employees}
              workItems={allResources.workItems}
              onOpenWorkItem={setSelectedWorkItemId}
              preview={workItemPreviews[previewKey(selectedWorkItem.id)]}
              onPreviewChange={(patch) => {
                const key = previewKey(selectedWorkItem.id);
                setWorkItemPreviews((previous) => ({ ...previous, [key]: { draft: '', ...previous[key], ...patch } }));
                if (patch.assigneeId !== undefined) setKanbanFilters((current) => ({ ...current, agent: 'ALL' }));
              }}
            />
          )}
        </WorkItemDialog>
      )}
    </div>
  );
}
