import { RecoveryNotice, useDraftRecovery } from './prd-editor/draftRecovery';
import { FormEvent, lazy, Suspense, UIEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowUp,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleUser,
  ClipboardList,
  Clock,
  ExternalLink,
  FileText,
  FolderPlus,
  GitFork,
  History,
  House,
  LayoutGrid,
  Loader2,
  LogOut,
  Menu,
  ImagePlus,
  Plus,
  Presentation,
  RefreshCw,
  Send,
  Server,
  Settings,
  ShieldCheck,
  Sparkles,
  Upload,
  Users,
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
  ImportedPrdSourceDto,
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
import { WorkspaceDashboard } from './WorkspaceDashboard';
import { openSharedWorkspace } from './openSharedWorkspace';
import { AdminUsersPage } from '../auth/AdminUsersPage';
import { SettingsPanel } from '../auth/SettingsPanel';
import { clearSession } from '../auth/session';
import { emptyResources, projectSpec, projectTitle, useWorkspaceProjects, type ResourceBundle } from './useWorkspaceProjects';
import { auditTitle, displayLabel, displayTime, progressDescription } from './presentation';
import { PrdReviewPanel } from './PrdReviewPanel';
import { getLatestPrd } from './prd';
import { getDraftPrototype, generateDraftPrototype, openDocument, getDraftPresentation, generateDraftPresentation, draftPresentationPreviewUrl } from './prd-editor/api';
import { isNearBottom } from './scrollPin';
import { appConfig } from './config';
import {
  createSession,
  autoRepairCommandJob,
  executeCommand,
  getLifecycleRoutes,
  getAgentBackend,
  getCommandJob,
  getSessionState,
  generatePrdPresentation,
  prdPresentationPreviewUrl,
  importPrdSource,
  isCommandJobAccepted,
  listAgentRuntime,
  listAgentRuntimeEvents,
  listAgentBackends,
  listWorkspaceChatMessages,
  loadWorkspaceChatImage,
  locateWorkspaceChatImage,
  recommendLifecycleRoutes,
  saveWorkspaceChatMessage,
  uploadWorkspaceChatImage,
  selectLifecycleRoute,
  selectAgentBackend,
} from './sessions';
import type { PrdPresentationAudience } from './sessions';
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
import type { PRDEditorHandle, PrdAgentProgress, PrdChatContext, PrdEditReceipt } from './prd-editor/PRDEditor';
import { isPrdRevisionInstruction } from './prd-editor/instructionRouting';

const EmbeddedPRDEditor = lazy(() => import('./prd-editor/PRDEditor').then(module => ({ default: module.PRDEditor })));

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

type WorkspaceTab = 'dashboard' | 'topLevel' | 'kanban' | 'flow' | 'audit' | 'prd';
type ChatTool = 'newProject' | 'resumeSession' | 'restoreSpec' | null;
type ProjectSourceMode = 'describe' | 'import';
type ChatMessage = {
  id: string;
  role: 'user' | 'agent';
  body: string;
  detail?: string;
  image_ids?: string[];
  createdAt: string;
};

function ChatMessageImage({ sessionId, imageId, actorId }: { sessionId: string; imageId: string; actorId: string }) {
  const [source, setSource] = useState('');
  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    void loadWorkspaceChatImage(sessionId, imageId, actorId).then(url => {
      objectUrl = url;
      if (cancelled) URL.revokeObjectURL(url);
      else setSource(url);
    }).catch(() => undefined);
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [sessionId, imageId, actorId]);
  return source ? <a href={source} target="_blank" rel="noreferrer" aria-label="查看完整 PRD 截图"><img className="ff-chat-image" src={source} alt="用户提供的 PRD 截图" /></a> : <span className="ff-chat-image-loading">正在读取截图…</span>;
}

const NEW_CHAT_KEY = '__new__';
import { currentActorId } from '../auth/session';

const CURRENT_ACTOR_ID = currentActorId() || 'owner-1';
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

function fileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('无法读取所选 PRD 文件。'));
    reader.onload = () => {
      const encoded = String(reader.result ?? '');
      const separator = encoded.indexOf(',');
      if (separator < 0) reject(new Error('无法读取所选 PRD 文件。'));
      else resolve(encoded.slice(separator + 1));
    };
    reader.readAsDataURL(file);
  });
}

function errorText(error: unknown): string {
  const normalized = normalizeNetworkError(error);
  return `${normalized.code}：${normalized.message}`;
}

/**
 * A failure that means "the backend was not ready for this yet" rather than
 * "this instruction is wrong". These are held in the queue and replayed
 * instead of being surfaced as an error bubble.
 */
function isDeferrableFailure(reason: unknown): boolean {
  const normalized = normalizeNetworkError(reason);
  return normalized.retryable
    || ['AGENT_UNAVAILABLE', 'COMMAND_IN_DOUBT', 'NETWORK_ERROR', 'REQUEST_TIMEOUT', 'WORKFLOW_ERROR'].includes(normalized.code);
}

async function rebuildQueuedImages(item: PendingInstruction): Promise<Array<{ file: File; preview: string }>> {
  return Promise.all(item.images.map(async (image) => {
    const blob = await (await fetch(image.preview)).blob();
    return {
      file: new File([blob], image.name || 'screenshot.png', { type: image.type || blob.type || 'image/png' }),
      preview: image.preview,
    };
  }));
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

/** Transient banner used by the top-level toolbar (PRD / prototype entry points). */
type PrototypeNotice = { tone: 'info' | 'warn' | 'error'; text: string };

/**
 * A user instruction held back while the backend is still busy with the
 * previous turn. Screenshots are kept as data URLs because the original File
 * cannot outlive a page reload.
 */
type PendingInstruction = {
  id: string;
  text: string;
  images: Array<{ name: string; type: string; preview: string }>;
  createdAt: number;
  /**
   * The chat already echoes this text (it was appended before the first
   * attempt failed), so replay must not append a second user bubble.
   */
  silentUserEcho?: boolean;
};

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
  const [projectSourceMode, setProjectSourceMode] = useState<ProjectSourceMode>('describe');
  const [importedPrd, setImportedPrd] = useState<ImportedPrdSourceDto | null>(null);
  const [importingPrd, setImportingPrd] = useState(false);
  const [ownerId, setOwnerId] = useState(CURRENT_ACTOR_ID);
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
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('dashboard');
  const [chatOpen, setChatOpen] = useState(true);
  const [chatTool, setChatTool] = useState<ChatTool>(null);
  const [chatMenuOpen, setChatMenuOpen] = useState(false);
  const [chatDraft, setChatDraft] = useState('');
  const chatRecovery = useDraftRecovery(activeSessionId ? `chat:${activeSessionId}` : null, chatDraft.trim() ? { text: chatDraft } : null);
  const [chatImages, setChatImages] = useState<Array<{ file: File; preview: string }>>([]);
  const chatImageInputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => { setChatImages([]); }, [activeSessionId]);
  const [showSettings, setShowSettings] = useState(false);
  const [showAdminModal, setShowAdminModal] = useState(false);
  const [agentBackends, setAgentBackends] = useState(DEFAULT_AGENT_BACKENDS);
  const [agentBackend, setAgentBackend] = useState<AgentBackendName>('codex');
  const [agentBackendMenuOpen, setAgentBackendMenuOpen] = useState(false);
  const [switchingAgentBackend, setSwitchingAgentBackend] = useState(false);
  const [chatMessages, setChatMessages] = useState<Record<string, ChatMessage[]>>({});
  const [chatHistoryLoaded, setChatHistoryLoaded] = useState<Record<string, boolean>>({});
  const [chatHistoryErrors, setChatHistoryErrors] = useState<Record<string, string>>({});
  const [chatHistoryRefresh, setChatHistoryRefresh] = useState(0);
  const [prdAgentProgress, setPrdAgentProgress] = useState<PrdAgentProgress | null>(null);
  const [prdChatContext, setPrdChatContext] = useState<PrdChatContext | null>(null);
  const [prdEditReceipt, setPrdEditReceipt] = useState<PrdEditReceipt | null>(null);
  const [agentRuntime, setAgentRuntime] = useState<AgentRuntimeDto[]>([]);
  const [topLevelRuntimeEvents, setTopLevelRuntimeEvents] = useState<AgentRuntimeEventDto[]>([]);
  const [lifecycleDecision, setLifecycleDecision] = useState<LifecycleRouteDecisionDto | null>(null);
  const [lifecycleLoading, setLifecycleLoading] = useState(false);
  const [kanbanFilters, setKanbanFilters] = useState(defaultWorkItemFilters);
  const [flowFilters, setFlowFilters] = useState(defaultWorkItemFilters);
  const [auditFilters, setAuditFilters] = useState(defaultWorkItemFilters);
  const [clipboardResult, setClipboardResult] = useState<string | null>(null);
  const [prototypeNotice, setPrototypeNotice] = useState<PrototypeNotice | null>(null);
  const [prototypeBusy, setPrototypeBusy] = useState(false);
  const [prototypePrompt, setPrototypePrompt] = useState<{ documentId: string; revision: number } | null>(null);
  const [presentationBusy, setPresentationBusy] = useState(false);
  const [presentationPrompt, setPresentationPrompt] = useState<{ documentId: string; revision: number; audience: PrdPresentationAudience } | null>(null);
  const pendingCommandIds = useRef(new Map<string, string>());
  const commandObservations = useRef(new Map<string, { close: () => void; completion: Promise<SessionStateDto> }>());
  const decompositionLifecycles = useRef(new Set<AbortController>());
  const reconciledDecompositionJobs = useRef(new Set<string>());
  const reconcilingDecompositionJobs = useRef(new Set<string>());
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  /**
   * Whether the conversation is pinned to the bottom. Background sync (polled
   * PRD progress / runtime summaries) changes state ~every second; blindly
   * scrolling then yanks the view back down while the user is reading history.
   * Only auto-scroll while the user is already at the bottom.
   */
  const chatPinnedRef = useRef(true);
  const chatWriteQueues = useRef(new Map<string, Promise<void>>());
  const unsavedChatMessages = useRef(new Map<string, Map<string, ChatMessage>>());
  const recordedPrdRuns = useRef(new Set<string>());
  const prdEditorRef = useRef<PRDEditorHandle | null>(null);
  const prdChatDetailsRef = useRef<HTMLDetailsElement | null>(null);
  const prdProgressClearTimerRef = useRef<number | null>(null);
  // Instructions typed while the backend is still executing the previous turn.
  // They queue above the composer instead of racing the running job, which is
  // what used to bounce back as a NETWORK_ERROR bubble.
  const [pendingInstructions, setPendingInstructions] = useState<PendingInstruction[]>([]);
  const [pendingQueueLoadedKey, setPendingQueueLoadedKey] = useState('');
  const [queueTick, setQueueTick] = useState(0);
  const [queuePrompt, setQueuePrompt] = useState<PendingInstruction | null>(null);
  const [queueInterruptBusy, setQueueInterruptBusy] = useState(false);
  const [queueNotice, setQueueNotice] = useState('');
  const flushingInstruction = useRef(false);
  const queueRetryTimer = useRef<number | null>(null);

  const decompositionJobKey = (sessionId: string) =>
    `firstflight.decomposition-job.${sessionId}`;
  const decompositionObservationKey = (sessionId: string, commandId: string) =>
    `${sessionId}:${commandId}`;

  const appendChatMessage = useCallback((
    sessionId: string | null,
    role: ChatMessage['role'],
    body: string,
    detail?: string,
    id?: string,
    imageIds?: string[],
  ) => {
    const key = sessionId ?? NEW_CHAT_KEY;
    const message: ChatMessage = {
      id: id ?? crypto.randomUUID(),
      role,
      body,
      detail,
      image_ids: imageIds,
      createdAt: new Date().toISOString(),
    };
    setChatMessages((previous) => previous[key]?.some(item => item.id === message.id)
      ? previous : { ...previous, [key]: [...(previous[key] ?? []), message] });
    if (sessionId) {
      const unsaved = unsavedChatMessages.current.get(sessionId) ?? new Map<string, ChatMessage>();
      unsaved.set(message.id, message);
      unsavedChatMessages.current.set(sessionId, unsaved);
      const previousWrite = chatWriteQueues.current.get(sessionId) ?? Promise.resolve();
      const write = previousWrite.then(async () => {
        await saveWorkspaceChatMessage(sessionId, ownerId, message);
        unsavedChatMessages.current.get(sessionId)?.delete(message.id);
        if (!unsavedChatMessages.current.get(sessionId)?.size) {
          setChatHistoryErrors(previous => ({ ...previous, [sessionId]: '' }));
        }
      }).catch(() => {
        setChatHistoryErrors(previous => ({ ...previous, [sessionId]: '有消息尚未保存到对话记录。' }));
      });
      chatWriteQueues.current.set(sessionId, write);
    }
  }, [ownerId]);

  useEffect(() => {
    if (!activeSessionId) return;
    const sessionId = activeSessionId;
    const controller = new AbortController();
    listWorkspaceChatMessages(sessionId, ownerId, controller.signal).then((stored) => {
      if (controller.signal.aborted) return;
      setChatMessages(previous => {
        const merged = new Map<string, ChatMessage>();
        for (const row of stored) merged.set(row.id, { ...row, detail: row.detail ?? undefined });
        for (const row of previous[sessionId] ?? []) if (!merged.has(row.id)) merged.set(row.id, row);
        return { ...previous, [sessionId]: [...merged.values()].sort((a, b) =>
          a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id)) };
      });
      setChatHistoryLoaded(previous => ({ ...previous, [sessionId]: true }));
      if (!unsavedChatMessages.current.get(sessionId)?.size) {
        setChatHistoryErrors(previous => ({ ...previous, [sessionId]: '' }));
      }
    }).catch(() => {
      if (controller.signal.aborted) return;
      setChatHistoryLoaded(previous => ({ ...previous, [sessionId]: true }));
      setChatHistoryErrors(previous => ({ ...previous, [sessionId]: '读取对话记录失败。' }));
    });
    return () => controller.abort();
  }, [activeSessionId, ownerId, chatHistoryRefresh]);

  async function retryChatHistory() {
    if (!activeSessionId) return;
    const sessionId = activeSessionId;
    const pending = [...(unsavedChatMessages.current.get(sessionId)?.values() ?? [])];
    const previousWrite = chatWriteQueues.current.get(sessionId) ?? Promise.resolve();
    const write = previousWrite.then(async () => {
      for (const message of pending) {
        await saveWorkspaceChatMessage(sessionId, ownerId, message);
        unsavedChatMessages.current.get(sessionId)?.delete(message.id);
      }
    }).catch(() => {
      setChatHistoryErrors(previous => ({ ...previous, [sessionId]: '对话记录仍未保存，请稍后重试。' }));
    });
    chatWriteQueues.current.set(sessionId, write);
    await write;
    setChatHistoryRefresh(value => value + 1);
  }

  const handlePrdAgentProgress = useCallback((progress: PrdAgentProgress | null) => {
    if (prdProgressClearTimerRef.current !== null) {
      window.clearTimeout(prdProgressClearTimerRef.current);
      prdProgressClearTimerRef.current = null;
    }
    if (!progress) {
      setPrdAgentProgress(current => current && ['completed', 'failed', 'cancelled'].includes(current.status) ? current : null);
      return;
    }
    setPrdAgentProgress(progress);
    if (['completed', 'failed', 'cancelled'].includes(progress.status)) {
      const messageId = `prd-run-${progress.runId}-${progress.status}`;
      if (activeSessionId && !recordedPrdRuns.current.has(messageId)) {
        recordedPrdRuns.current.add(messageId);
        appendChatMessage(activeSessionId, 'agent',
          progress.status === 'completed' ? `${progress.title}已完成`
            : progress.status === 'failed' ? `${progress.title}失败` : `${progress.title}已取消`,
          progress.detail, messageId);
      }
      prdProgressClearTimerRef.current = window.setTimeout(() => {
        setPrdAgentProgress(null);
        prdProgressClearTimerRef.current = null;
      }, 8_000);
    }
  }, [activeSessionId, appendChatMessage]);

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
    if (prdProgressClearTimerRef.current !== null) window.clearTimeout(prdProgressClearTimerRef.current);
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
  const prdProgressActive = prdAgentProgress?.status === 'queued' || prdAgentProgress?.status === 'running';
  /**
   * Single source of truth for "the agent is occupied". Previously only the
   * local `busy` flag gated the composer, so a running PRD generation left the
   * input enabled and the next instruction was fired into an occupied
   * single-worker backend and rejected as NETWORK_ERROR.
   *
   * `activeRuntime.status` is deliberately NOT part of this: it is a persisted
   * backend status polled from the DB, so a stale `running` row would block the
   * composer forever. Genuinely occupied turns are covered by the local `busy`
   * flag plus the polled PRD progress, and anything that still races the
   * backend is absorbed by the deferral path in `submitChat`.
   */
  const agentBusy = busy
    || projectsLoading
    || lifecycleLoading
    || observingDecomposition
    || prdProgressActive;
  const agentBusyReason = busy
    ? workflowProgress || '正在执行上一条指令'
    : prdProgressActive
      ? `正在生成 PRD${prdAgentProgress?.currentBlockTitle ? `：${prdAgentProgress.currentBlockTitle}` : ''}`
      : observingDecomposition
        ? '正在拆分工作项'
        : lifecycleLoading
          ? '正在准备 SDLC 路线选择'
          : '正在加载项目';

  // Opening or switching a conversation should start at the latest turn.
  useEffect(() => {
    chatPinnedRef.current = true;
  }, [activeSessionId]);

  /** Stay pinned only while the reader is already near the bottom. */
  function handleChatScroll(event: UIEvent<HTMLDivElement>) {
    chatPinnedRef.current = isNearBottom(event.currentTarget);
  }

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const scroll = chatScrollRef.current;
      if (scroll && chatPinnedRef.current) scroll.scrollTop = scroll.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [
    activeSessionId,
    activeRuntime?.current_summary,
    busy,
    chatTool,
    prdAgentProgress?.detail,
    prdAgentProgress?.stage,
    prdAgentProgress?.activitySteps,
    state?.phase,
    visibleChatMessages.length,
    workflowProgress,
  ]);

  useEffect(() => {
    if (activeTab !== 'prd') return undefined;
    const frame = window.requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    return () => window.cancelAnimationFrame(frame);
  }, [activeTab, chatOpen]);

  const projectList = useMemo(() => Object.values(projects), [projects]);
  const dashboardProjects = useMemo(() => projectList.filter((project) => {
    const root = project.resources.workItems.find((item) => item.kind === 'ROOT');
    return !root?.suggested_assignee || root.suggested_assignee === CURRENT_ACTOR_ID;
  }), [projectList]);
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
  /** PRD/prototype endpoints are keyed by the project's root work item. */
  const currentRootWorkItemId = currentProject?.resources.workItems.find((item) => item.kind === 'ROOT')?.id
    ?? catalog.find((project) => project.session_id === activeSessionId)?.root_work_item_id
    ?? null;
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
    if (projectSourceMode === 'import' && !importedPrd) {
      setError('请先选择并成功导入一份现有 PRD。');
      return;
    }
    setBusy(true);
    setError(null);
    const sourcePrd = projectSourceMode === 'import' ? importedPrd : null;
    const brief: ProjectBriefDto = {
      motivation,
      final_objective: objective,
      known_scope: lines(scope),
      exclusions: [],
      reference_materials: sourcePrd ? [`导入基线 PRD：${sourcePrd.filename}`] : [],
      expected_deliverables: lines(deliverables),
      time_constraints: '第一阶段未指定',
      staffing_constraints: '第一阶段未指定',
      final_approver: ownerId,
      project_manager_ids: [ownerId],
      root_owner_ids: [ownerId],
      source_prd: sourcePrd,
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
        [sourcePrd ? `基于 ${sourcePrd.filename} 修订` : '', motivation.trim(), ...lines(scope), ...lines(deliverables)].filter(Boolean).join(' · '),
      );
      updateSessionState(created);
      selectSession(created.session_id);
      setChatTool(null);
      setMotivation('');
      setObjective('');
      setScope('');
      setDeliverables('');
      setProjectSourceMode('describe');
      setImportedPrd(null);
      await refreshResources(created.session_id);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorkflowProgress(null);
      setBusy(false);
    }
  }

  async function onImportPrdFile(file: File) {
    setImportingPrd(true);
    setImportedPrd(null);
    setError(null);
    try {
      if (file.size > 24 * 1024 * 1024) throw new Error('PRD 文件不能超过 24 MB，请压缩图片或拆分附件后重试。');
      const contentBase64 = await fileAsBase64(file);
      const imported = await importPrdSource(file.name, contentBase64);
      setImportedPrd(imported);
      setMotivation((current) => current.trim() || '基于导入的现有 PRD，审查并修正不完整、不一致或不准确的内容');
      setObjective((current) => current.trim() || `修订并完善 ${imported.title}`);
      setDeliverables((current) => current.trim() || '经修订并可人工审核的 PRD');
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setImportingPrd(false);
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
    if (action === 'create_spec' || (action === 'revise' && resources.specs.some(spec =>
      spec.id === state.current_spec_version_id && spec.generation_source === 'BLOCK_EDITOR'))) {
      setSelectedWorkItemId(null);
      setActiveTab('prd');
      return;
    }
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
      const refreshedState = await getSessionState(state.session_id);
      updateSessionState(refreshedState);
      appendChatMessage(
        state.session_id,
        'agent',
        `已确认顶层路线：${decision.options.find((option) => option.model === model)?.name ?? model}`,
        '已在右侧工作区打开 PRD 编辑器；PRD 审核通过后再展开完整任务规划。',
      );
      setWorkflowProgress('路线已确认，正在打开 Block 编辑器…');
      setSelectedWorkItemId(null);
      setActiveTab('prd');
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

  async function runGlobalInstruction(message: string, imageIds: string[] = []) {
    const normalized = message.trim().replace(/[。！!？?，,]/g, '').toLowerCase();
    if (imageIds.length && /(?:代码|源文件|前端页面|后端接口|后端服务|数据库表|迁移脚本|看板|工单|Agent\s*Spec|顶层图|任务流转图|审计记录)/i.test(message)
      && !/(?:\bPRD\b|产品需求文档|当前文档|文档里)/i.test(message)) {
      appendChatMessage(state?.session_id ?? null, 'agent', '截图已保存到对话记录。当前截图定位支持 PRD 文章；若要修改其他页面，请在指令中直接说明目标。');
      return;
    }
    const navigation: Array<{ phrases: string[]; tab: Exclude<WorkspaceTab, 'prd'>; reply: string }> = [
      { phrases: ['打开dashboard', '打开仪表盘', '查看总览', '首页', '仪表盘'], tab: 'dashboard', reply: '已打开个人 Dashboard。' },
      { phrases: ['打开顶层图', '查看顶层图', '顶层图'], tab: 'topLevel', reply: '已打开当前任务的 SDLC 顶层图。' },
      { phrases: ['打开看板', '查看看板', '任务看板', '看板'], tab: 'kanban', reply: '已打开任务看板。' },
      { phrases: ['打开流转图', '查看流转图', '任务流转图'], tab: 'flow', reply: '已打开任务依赖流转图。' },
      { phrases: ['打开审计记录', '查看审计记录', '审计记录'], tab: 'audit', reply: '已打开审计记录。' },
    ];
    const destination = navigation.find((item) => item.phrases.includes(normalized));
    if (destination) {
      navigateToTab(destination.tab);
      appendChatMessage(state?.session_id ?? null, 'agent', destination.reply);
      return;
    }
    if (['打开prd', '编写prd', '打开prd编辑器', '编辑prd'].includes(normalized) && state) {
      setSelectedWorkItemId(null);
      setActiveTab('prd');
      appendChatMessage(state.session_id, 'agent', '已在右侧工作区打开 PRD 编辑器。');
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

    if (activeTab === 'prd' && ['修订prd', '生成修订版'].includes(normalized)) {
      appendChatMessage(state.session_id, 'agent', '请说明要修改的 PRD 章节和具体要求，例如“把项目目标部分补充量化指标”。');
      return;
    }
    if (activeTab === 'prd' && (isPrdRevisionInstruction(message, true) || imageIds.length > 0)) {
      setBusy(true);
      setError(null);
      setWorkflowProgress(imageIds.length ? '正在用 Codex 识别截图并定位 PRD 段落…'
        : /所有|全部|批量|统一/.test(message) && /替换|改为|改成|换成/.test(message)
          ? '正在识别批量规则并列出命中与例外…'
          : /图片|插画|插图|配图/.test(message)
            ? '正在生成配图并准备修改预览…'
            : '正在定位 PRD 目标并规划修改…');
      try {
        if (!prdEditorRef.current) throw new Error('PRD 编辑器正在打开，请稍后重试。');
        const screenshot = imageIds.length ? await locateWorkspaceChatImage(state.session_id, imageIds[0], ownerId) : undefined;
        if (screenshot) setWorkflowProgress('已识别截图，正在核对原文并提交修改…');
        const title = await prdEditorRef.current.requestRevision(message, screenshot);
        appendChatMessage(state.session_id, 'agent', /\[prd-(?:action|job):/.test(title) || title.startsWith('已打开') ? title : title.startsWith('待确认：')
          ? `${title}。请在右侧查看影响范围并应用选中的修改。`
          : `已将修改指令交给「${title}」章节的 Agent。`,
          /\[prd-(?:action|job):/.test(title) ? '可从本条消息打开保存结果；生成中的候选会在完成后供你预览，正文不会自动应用。' : title.startsWith('待确认：') ? '请检查右侧预览；确认后这次指令的改动会合并为一个全文版本。' : '生成进度与结果会显示在右侧 PRD；如有未保存输入，先保存再修改。');
      } catch (reason) {
        if (isDeferrableFailure(reason) && imageIds.length === 0) {
          // The backend is still occupied by the previous turn. Hold the
          // instruction instead of pushing a NETWORK_ERROR bubble back.
          deferInstruction(message, [], reason, true);
        } else {
          appendChatMessage(state.session_id, 'agent', '这条 PRD 修改指令未能执行', errorText(reason));
        }
      } finally {
        setWorkflowProgress(null);
        setBusy(false);
      }
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

    if (state.legal_actions.includes('message')) {
      await runAction('message', message);
      return;
    }

    if (activeTab === 'prd' && ['NEED_CLARIFICATION', 'SPECIFICATION', 'REVIEW'].includes(state.phase) && !requestedAction) {
      appendChatMessage(
        state.session_id,
        'agent',
        '当前 PRD 已打开，请直接说明要修改的章节、段落或图表；也可以先在右侧选中内容，再描述修改要求。',
        '如果目标是其他页面或项目资源，请在指令中写明目标与操作。',
      );
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

  const pendingQueueKey = activeSessionId
    ? `firstflight.pending-instructions.${appConfig.apiBaseUrl}.${encodeURIComponent(ownerId)}.${activeSessionId}`
    : '';

  useEffect(() => {
    if (!pendingQueueKey) {
      setPendingQueueLoadedKey('');
      return;
    }
    let stored: PendingInstruction[] = [];
    try {
      const raw = localStorage.getItem(pendingQueueKey);
      const parsed: unknown = raw ? JSON.parse(raw) : [];
      if (Array.isArray(parsed)) {
        stored = parsed.filter((item): item is PendingInstruction => Boolean(item)
          && typeof (item as PendingInstruction).id === 'string'
          && typeof (item as PendingInstruction).text === 'string'
          && Array.isArray((item as PendingInstruction).images));
      }
    } catch {
      stored = [];
    }
    setPendingInstructions(stored);
    setQueuePrompt(null);
    setPendingQueueLoadedKey(pendingQueueKey);
  }, [pendingQueueKey]);

  useEffect(() => {
    // Persist only after this session's queue has been read back, otherwise the
    // empty initial state would wipe an unread stored queue.
    if (!pendingQueueKey || pendingQueueLoadedKey !== pendingQueueKey) return;
    try {
      if (pendingInstructions.length) localStorage.setItem(pendingQueueKey, JSON.stringify(pendingInstructions));
      else localStorage.removeItem(pendingQueueKey);
    } catch {
      /* 本机存储不可用时只影响刷新后的恢复。 */
    }
  }, [pendingQueueKey, pendingQueueLoadedKey, pendingInstructions]);

  useEffect(() => {
    if (!queueNotice) return undefined;
    const timer = window.setTimeout(() => setQueueNotice(''), 8_000);
    return () => window.clearTimeout(timer);
  }, [queueNotice]);

  useEffect(() => () => {
    if (queueRetryTimer.current !== null) window.clearTimeout(queueRetryTimer.current);
  }, []);

  function deferInstruction(
    text: string,
    images: Array<{ file: File; preview: string }>,
    reason?: unknown,
    silentUserEcho = false,
  ) {
    const item: PendingInstruction = {
      id: crypto.randomUUID(),
      text,
      images: images.map((image) => ({ name: image.file.name, type: image.file.type, preview: image.preview })),
      createdAt: Date.now(),
      silentUserEcho: silentUserEcho || undefined,
    };
    setPendingInstructions((previous) => [...previous, item]);
    setError(null);
    setQueueNotice(reason
      ? '后端仍在执行上一轮任务，这条指令已排入队列，任务结束后自动执行。'
      : '当前任务执行中，这条指令已排入队列，任务结束后自动执行。');
    return item;
  }

  function removeInstruction(id: string) {
    setPendingInstructions((previous) => previous.filter((item) => item.id !== id));
    setQueuePrompt((current) => (current?.id === id ? null : current));
  }

  function promoteInstruction(id: string) {
    setPendingInstructions((previous) => {
      const target = previous.find((item) => item.id === id);
      return target ? [target, ...previous.filter((item) => item.id !== id)] : previous;
    });
  }

  /** The only path that talks to the backend. Never called while agentBusy. */
  async function dispatchInstruction(
    text: string,
    images: Array<{ file: File; preview: string }>,
    options: { silentUserEcho?: boolean } = {},
  ) {
    let imageIds: string[] = [];
    if (images.length && state) {
      setBusy(true);
      setWorkflowProgress('正在上传 PRD 截图…');
      try {
        for (const image of images) {
          const uploaded = await uploadWorkspaceChatImage(state.session_id, ownerId, image.file.type, await fileAsBase64(image.file));
          imageIds.push(uploaded.id);
        }
      } finally {
        setBusy(false);
        setWorkflowProgress(null);
      }
    }
    if (!options.silentUserEcho) {
      appendChatMessage(state?.session_id ?? null, 'user', text, undefined, undefined, imageIds);
    }
    await runGlobalInstruction(text, imageIds);
  }

  async function submitChat(event: FormEvent) {
    event.preventDefault();
    const message = chatDraft.trim();
    if (!message || health !== 'ok') return;
    if (chatImages.length && (!state || activeTab !== 'prd')) {
      setError('请先打开已有项目的 PRD，再发送截图修改指令。');
      return;
    }
    const images = chatImages;
    // The user just acted: always bring the newest turn into view.
    chatPinnedRef.current = true;
    setChatDraft('');
    setChatImages([]);
    setChatMenuOpen(false);
    setAgentBackendMenuOpen(false);
    // FIFO. While a job runs — or while earlier instructions are still waiting —
    // never race the single-worker backend; hold the instruction instead.
    if (agentBusy || pendingInstructions.length > 0) {
      deferInstruction(message, images);
      return;
    }
    try {
      await dispatchInstruction(message, images);
    } catch (reason) {
      if (isDeferrableFailure(reason)) {
        deferInstruction(message, images, reason, true);
      } else {
        setError(errorText(reason));
      }
    }
  }

  /**
   * Drains the queue one instruction at a time. A deferrable failure means the
   * backend was still occupied, so the item goes back to the head and waits.
   */
  useEffect(() => {
    if (!pendingQueueKey || pendingQueueLoadedKey !== pendingQueueKey) return;
    if (!pendingInstructions.length || agentBusy || flushingInstruction.current) return;
    const next = pendingInstructions[0];
    flushingInstruction.current = true;
    setPendingInstructions((previous) => previous.filter((item) => item.id !== next.id));
    void (async () => {
      let retryLater = false;
      try {
        await dispatchInstruction(next.text, await rebuildQueuedImages(next), { silentUserEcho: next.silentUserEcho });
      } catch (reason) {
        if (isDeferrableFailure(reason)) {
          retryLater = true;
          setPendingInstructions((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
          if (queueRetryTimer.current !== null) window.clearTimeout(queueRetryTimer.current);
          queueRetryTimer.current = window.setTimeout(() => {
            queueRetryTimer.current = null;
            setQueueTick((tick) => tick + 1);
          }, 5_000);
        } else {
          appendChatMessage(state?.session_id ?? null, 'agent', '这条排队指令未能执行', errorText(reason));
        }
      } finally {
        flushingInstruction.current = false;
        if (!retryLater) setQueueTick((tick) => tick + 1);
      }
    })();
  }, [agentBusy, pendingInstructions, pendingQueueKey, pendingQueueLoadedKey, queueTick]);

  async function applyQueuedInstructionNow(item: PendingInstruction) {
    setQueuePrompt(null);
    setQueueInterruptBusy(true);
    let cancelledRuns = 0;
    try {
      cancelledRuns = (await prdEditorRef.current?.cancelActiveRuns()) ?? 0;
    } catch {
      cancelledRuns = 0;
    } finally {
      setQueueInterruptBusy(false);
    }
    promoteInstruction(item.id);
    setQueueNotice(cancelledRuns > 0
      ? `已请求取消 ${cancelledRuns} 个正在执行的生成任务，取消生效后这条指令会自动发出。`
      : '当前任务没有可用的取消入口，这条指令已排到队首，会在任务结束后立刻执行。');
    setQueueTick((tick) => tick + 1);
  }

  async function addChatImages(files: File[]) {
    const allowed = files.filter(file => ['image/png', 'image/jpeg', 'image/webp'].includes(file.type));
    if (allowed.length !== files.length || allowed.some(file => file.size > 5 * 1024 * 1024)) {
      setError('仅支持单张不超过 5 MB 的 PNG、JPEG 或 WebP 截图。');
      return;
    }
    if (chatImages.length + allowed.length > 1) {
      setError('一次修改请只贴一张截图，以便准确定位段落。');
      return;
    }
    const previews = await Promise.all(allowed.map(async file => ({
      file, preview: `data:${file.type};base64,${await fileAsBase64(file)}`,
    })));
    setChatImages(previous => [...previous, ...previews]);
    setError(null);
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

  function navigateToTab(tab: Exclude<WorkspaceTab, 'prd'>) {
    if (activeTab !== 'prd') {
      setActiveTab(tab);
      return;
    }
    const close = () => setActiveTab(tab);
    if (prdEditorRef.current) prdEditorRef.current.requestClose(close);
    else close();
  }

  function openPrdEditor() {
    if (!state) return;
    setSelectedWorkItemId(null);
    setActiveTab('prd');
  }

  /**
   * Open the project's HTML prototype in a new browser tab.
   *
   * Reuses the existing PRD prototype contract: `GET /prd/{wi}` carries the
   * bound prototype and its `content_url` is served by
   * `GET /prd/{wi}/v/{version}/prototype`. Generation itself is kicked off by
   * the backend once the PRD is frozen, so this only reports progress.
   */
  async function openPrototype() {
    if (!currentRootWorkItemId) {
      setPrototypeNotice({ tone: 'warn', text: '请先选择一个任务。' });
      return;
    }
    // 已定稿：沿用既有冻结版原型（进入下一阶段后自动生成的那条）。
    if (state?.current_spec_version_id) {
      setPrototypeBusy(true);
      setPrototypeNotice(null);
      try {
        const document_ = await getLatestPrd(currentRootWorkItemId);
        const prototype = document_.prototype ?? null;
        if (prototype?.status === 'ready' && prototype.content_url) {
          window.open(`${appConfig.apiBaseUrl}${prototype.content_url}`, '_blank', 'noopener,noreferrer');
          return;
        }
        if (prototype?.status === 'failed') {
          setPrototypeNotice({
            tone: 'error',
            text: `HTML 原型生成失败：${prototype.error_message || prototype.error_code || '未知原因'}`,
          });
          return;
        }
        setPrototypeNotice({ tone: 'info', text: 'HTML 原型还在生成中，请稍候再试。' });
      } catch (reason) {
        const error = normalizeNetworkError(reason);
        setPrototypeNotice({
          tone: 'error',
          text: error.status === 404
            ? '当前任务还没有可查看的 PRD 版本，无法打开 HTML 原型。请先在 PRD 页完成并提交 PRD。'
            : `暂时无法读取 HTML 原型：${error.message}`,
        });
      } finally {
        setPrototypeBusy(false);
      }
      return;
    }
    // 未定稿：不再硬性要求「进入下一阶段」；改为基于当前 PRD 草稿生成（草稿版）。
    if (!state?.session_id) return;
    setPrototypeBusy(true);
    setPrototypeNotice(null);
    try {
      const snapshot = await openDocument(state.session_id, false);
      const documentId = snapshot.document.id;
      const existing = await getDraftPrototype(documentId);
      if (existing.status === 'ready' && existing.content_url) {
        window.open(`${appConfig.apiBaseUrl}${existing.content_url}`, '_blank', 'noopener,noreferrer');
        return;
      }
      setPrototypePrompt({ documentId, revision: snapshot.document.revision });
    } catch (reason) {
      const error = normalizeNetworkError(reason);
      setPrototypeNotice({
        tone: 'error',
        text: error.status === 404
          ? '当前任务还没有可查看的 PRD 草稿，无法生成 HTML 原型。请先在 PRD 页生成正文。'
          : `暂时无法读取 PRD 草稿：${error.message}`,
      });
    } finally {
      setPrototypeBusy(false);
    }
  }

  /** 确认后基于当前 PRD 草稿生成 HTML 原型（与冻结版同一 Agent/skill 路线）。 */
  async function confirmDraftPrototype() {
    const prompt = prototypePrompt;
    if (!prompt) return;
    setPrototypeBusy(true);
    setPrototypeNotice({ tone: 'info', text: `正在基于当前 PRD 草稿（第 ${prompt.revision} 版）生成 HTML 原型草稿…` });
    try {
      const result = await generateDraftPrototype(prompt.documentId);
      setPrototypePrompt(null);
      if (result.content_url) {
        window.open(`${appConfig.apiBaseUrl}${result.content_url}`, '_blank', 'noopener,noreferrer');
      }
    } catch (reason) {
      setPrototypeNotice({ tone: 'error', text: `HTML 原型草稿生成失败：${normalizeNetworkError(reason).message}` });
    } finally {
      setPrototypeBusy(false);
    }
  }

  async function createPrdPresentation(audience: PrdPresentationAudience) {
    const label = audience === 'team' ? '团队' : '对外';
    const phrase = audience === 'team' ? '团队讲解' : '对外介绍';
    // 已定稿：走冻结版编排，随后在新标签页打开在线预览页（页内下载）。
    if (state?.current_spec_version_id) {
      setPresentationBusy(true);
      setPrototypeNotice({ tone: 'info', text: `正在制作${phrase} PPT…` });
      try {
        await generatePrdPresentation(state.session_id, ownerId, audience, state.current_spec_version_id);
        window.open(prdPresentationPreviewUrl(state.session_id, audience), '_blank', 'noopener,noreferrer');
        setPrototypeNotice({ tone: 'info', text: `${label} PPT 已生成，已在新标签页打开预览页，可在其中下载可编辑 PPTX。` });
      } catch (reason) {
        setPrototypeNotice({ tone: 'error', text: `PPT 生成失败：${errorText(reason)}` });
      } finally {
        setPresentationBusy(false);
      }
      return;
    }
    // 未定稿：不再硬性要求「进入下一阶段」；改为基于当前 PRD 草稿生成（草稿版，走同一编排流程）。
    if (!state?.session_id) return;
    setPresentationBusy(true);
    setPrototypeNotice(null);
    try {
      const snapshot = await openDocument(state.session_id, false);
      const documentId = snapshot.document.id;
      const existing = await getDraftPresentation(documentId, audience);
      if (existing.status === 'ready') {
        window.open(draftPresentationPreviewUrl(documentId, audience), '_blank', 'noopener,noreferrer');
        setPrototypeNotice({ tone: 'info', text: `已打开基于当前 PRD 草稿（第 ${existing.revision} 版）的${label} PPT 预览页，可在其中下载。` });
        return;
      }
      setPresentationPrompt({ documentId, revision: snapshot.document.revision, audience });
    } catch (reason) {
      const error = normalizeNetworkError(reason);
      setPrototypeNotice({
        tone: 'error',
        text: error.status === 404
          ? '当前任务还没有可查看的 PRD 草稿，无法生成 PPT。请先在 PRD 页生成正文。'
          : `暂时无法读取 PRD 草稿：${error.message}`,
      });
    } finally {
      setPresentationBusy(false);
    }
  }

  /** 确认后基于当前 PRD 草稿生成团队/对外 PPT，并在新标签页打开在线预览页（页内下载）。 */
  async function confirmDraftPresentation() {
    const prompt = presentationPrompt;
    if (!prompt) return;
    const label = prompt.audience === 'team' ? '团队' : '对外';
    setPresentationBusy(true);
    setPrototypeNotice({ tone: 'info', text: `正在基于当前 PRD 草稿（第 ${prompt.revision} 版）生成${label} PPT…` });
    try {
      await generateDraftPresentation(prompt.documentId, prompt.audience);
      setPresentationPrompt(null);
      window.open(draftPresentationPreviewUrl(prompt.documentId, prompt.audience), '_blank', 'noopener,noreferrer');
      setPrototypeNotice({ tone: 'info', text: `${label} PPT 已生成，已在新标签页打开预览页，可在其中下载；同一草稿修订号再次点击会直接打开预览页。` });
    } catch (reason) {
      setPrototypeNotice({ tone: 'error', text: `${label} PPT 生成失败：${errorText(reason)}` });
    } finally {
      setPresentationBusy(false);
    }
  }

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
    setPrdAgentProgress(null);
    setPrototypeNotice(null);
    setPrdChatContext(null);
    setPrdEditReceipt(null);
    setPresentationPrompt(null);
    setError(null);
  }

  function requestConversationSwitch(sessionId: string | null) {
    if (sessionId === activeSessionId) return;
    const switchNow = () => {
      switchConversation(sessionId);
      if (activeTab === 'prd') setActiveTab('dashboard');
    };
    if (activeTab === 'prd' && prdEditorRef.current) prdEditorRef.current.requestClose(switchNow);
    else switchNow();
  }

  function startNewTask() {
    const start = () => {
      switchConversation(null);
      setChatTool(null);
      setChatOpen(true);
      if (activeTab === 'prd') setActiveTab('dashboard');
    };
    if (activeTab === 'prd' && prdEditorRef.current) prdEditorRef.current.requestClose(start);
    else start();
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
          className={'ff-activity-button ' + (activeTab === 'dashboard' ? 'is-active' : '')}
          onClick={() => navigateToTab('dashboard')}
          title="Dashboard 总览"
          aria-label="打开 Dashboard 总览"
          aria-pressed={activeTab === 'dashboard'}
        >
          <House aria-hidden="true" />
        </button>
        <button
          className={'ff-activity-button ' + (activeTab === 'topLevel' ? 'is-active' : '')}
          onClick={() => navigateToTab('topLevel')}
          title="顶层图"
          aria-label="打开顶层图"
          aria-pressed={activeTab === 'topLevel'}
        >
          <GitFork aria-hidden="true" />
        </button>
        <button
          className={'ff-activity-button ' + (activeTab === 'kanban' ? 'is-active' : '')}
          onClick={() => navigateToTab('kanban')}
          title="敏捷任务看板"
          aria-label="打开敏捷任务看板"
          aria-pressed={activeTab === 'kanban'}
        >
          <LayoutGrid aria-hidden="true" />
          {allResources.workItems.length > 0 && <span className="ff-activity-count">{allResources.workItems.length}</span>}
        </button>
        <button
          className={'ff-activity-button ' + (activeTab === 'flow' ? 'is-active' : '')}
          onClick={() => navigateToTab('flow')}
          title="任务流转图"
          aria-label="打开任务流转图"
          aria-pressed={activeTab === 'flow'}
        >
          <GitFork aria-hidden="true" />
        </button>
        <button
          className={'ff-activity-button ' + (activeTab === 'audit' ? 'is-active' : '')}
          onClick={() => navigateToTab('audit')}
          title="审计记录"
          aria-label="打开审计记录"
          aria-pressed={activeTab === 'audit'}
        >
          <ClipboardList aria-hidden="true" />
        </button>
        {state && <button
          className={'ff-activity-button ' + (activeTab === 'prd' ? 'is-active' : '')}
          onClick={openPrdEditor}
          title="编写 PRD"
          aria-label="编写 PRD"
          aria-pressed={activeTab === 'prd'}
        >
          <FileText aria-hidden="true" />
        </button>}
        <div className="ff-activity-spacer" />
        <button className="ff-activity-button" title="系统运行状态" aria-label="系统运行状态">
          <Activity aria-hidden="true" />
        </button>
        <button
          className={'ff-activity-button ' + (showSettings ? 'is-active' : '')}
          title="设置"
          aria-label="设置"
          aria-expanded={showSettings}
          onClick={() => setShowSettings((v) => !v)}
        >
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
                onChange={(event) => requestConversationSwitch(event.target.value || null)}
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

          <div ref={chatScrollRef} className="ff-chat-scroll" role="log" aria-label="Agent 对话与工作记录" onScroll={handleChatScroll}>
            {activeSessionId && !chatHistoryLoaded[activeSessionId] && <p className="ff-chat-history-note">正在加载对话记录…</p>}
            {activeSessionId && chatHistoryErrors[activeSessionId] && <p className="ff-chat-history-note" role="alert">{chatHistoryErrors[activeSessionId]} <button type="button" onClick={() => void retryChatHistory()}>重试</button></p>}
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
                    <strong>{message.body.replace(/\n\[prd-(?:action|job):[^\]]+\]/g, '')}</strong>
                    {activeTab === 'prd' && message.role === 'agent' && /\[prd-action:([^\]]+)\]/.test(message.body) && <button type="button" onClick={() => prdEditorRef.current?.openSavedAction(message.body.match(/\[prd-action:([^\]]+)\]/)![1])}>打开此操作的保存结果</button>}
                    {activeTab === 'prd' && message.role === 'agent' && (/\[prd-job:/.test(message.body) || message.id.startsWith('prd-run-prdjob_')) && <button type="button" onClick={() => prdEditorRef.current?.openTasks()}>定位候选任务与预览</button>}
                    {message.detail && <p>{message.detail}</p>}
                    {activeSessionId && message.image_ids?.map(imageId =>
                      <ChatMessageImage key={imageId} sessionId={activeSessionId} imageId={imageId} actorId={ownerId} />)}
                  </div>
                </div>
                {message.role === 'user' && <span className="ff-avatar ff-avatar-user">U</span>}
              </div>
            ))}

            {state && chatHistoryLoaded[activeSessionId ?? ''] && visibleChatMessages.length === 0 && (
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

            {lifecycleDecision && !lifecycleDecision.selected_model && (
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

            {prdAgentProgress && (
              <div className="ff-message ff-message-agent ff-thinking-message ff-prd-agent-progress" aria-live="polite">
                <span className={`ff-avatar ${prdProgressActive ? 'ff-avatar-thinking' : ''}`}>
                  {prdProgressActive ? <Loader2 className="ff-spin" aria-hidden="true" /> : prdAgentProgress.status === 'failed' ? '!' : <CheckCircle2 aria-hidden="true" />}
                </span>
                <div className="ff-message-content">
                  <p className="ff-message-meta">PRD 编写 Agent · {prdProgressActive ? '工作播报' : '阶段结果'}</p>
                  <div className={`ff-message-bubble ff-thinking-bubble${prdAgentProgress.status === 'failed' ? ' is-error' : ''}`}>
                    <strong>{prdAgentProgress.title} · {prdAgentProgress.stage}</strong>
                    <p>{prdAgentProgress.currentBlockTitle
                      ? `${prdProgressActive ? '我正在处理' : '刚刚处理了'}「${prdAgentProgress.currentBlockTitle}」。${prdAgentProgress.detail}`
                      : prdAgentProgress.detail}</p>
                    {typeof prdAgentProgress.elapsedSeconds === 'number' && <small>已记录耗时 {prdAgentProgress.elapsedSeconds.toFixed(1)} 秒</small>}
                    {prdProgressActive && Boolean(prdAgentProgress.activitySteps?.length) && <ol className="ff-prd-activity-steps" aria-label="Agent 当前处理步骤">
                      {prdAgentProgress.activitySteps?.map((step, index, steps) => <li key={`${index}:${step}`} className={index === steps.length - 1 ? 'is-current' : ''}>{step}</li>)}
                    </ol>}
                    {prdProgressActive && prdAgentProgress.activityText && <div className="ff-prd-token-stream" role="status" aria-label={`实时处理流：${prdAgentProgress.activityText}`}>
                      <span key={prdAgentProgress.activityText} aria-hidden="true">{prdAgentProgress.activityText}<i>　·　</i>{prdAgentProgress.activityText}</span>
                    </div>}
                    {typeof prdAgentProgress.completed === 'number' && typeof prdAgentProgress.total === 'number' && prdAgentProgress.total > 0 && <>
                      <div className="ff-prd-progress-track" aria-label={`PRD 进度 ${prdAgentProgress.completed}/${prdAgentProgress.total}`}><i style={{ width: `${Math.max(4, Math.min(100, prdAgentProgress.completed / prdAgentProgress.total * 100))}%` }} /></div>
                      <div className="ff-runtime-meta"><span>当前进度 {prdAgentProgress.completed}/{prdAgentProgress.total}</span>{prdAgentProgress.currentBlockTitle && <span>{prdAgentProgress.currentBlockTitle}</span>}</div>
                    </>}
                  </div>
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

            {activeRuntime?.status === 'error' && !busy && (
              <div className="ff-agent-event is-error">
                <span className="ff-event-check"><AlertCircle aria-hidden="true" /></span>
                <div><strong>Agent 运行异常</strong><p>{runtimeOperationLabels[activeRuntime.current_operation] ?? activeRuntime.current_operation} · {activeRuntime.current_summary || `累计调用 ${activeRuntime.call_count} 次`}</p></div>
              </div>
            )}

            {((activeRuntime?.status === 'completed' && !busy) || (state && resources.events.length > 0)) && (
              <details className="ff-agent-events-group">
                <summary>
                  <span className="ff-event-check"><CheckCircle2 aria-hidden="true" /></span>
                  <strong>处理记录 · {(activeRuntime?.status === 'completed' && !busy ? 1 : 0) + (state ? Math.min(3, resources.events.length) : 0)} 条</strong>
                  <ChevronDown size={14} aria-hidden="true" />
                </summary>
                {activeRuntime?.status === 'completed' && !busy && (
                  <div className="ff-agent-event">
                    <span className="ff-event-check"><CheckCircle2 aria-hidden="true" /></span>
                    <div>
                      <strong>Agent 已完成本阶段处理</strong>
                      <p>{runtimeOperationLabels[activeRuntime.current_operation] ?? activeRuntime.current_operation} · {activeRuntime.current_summary || `累计调用 ${activeRuntime.call_count} 次`}</p>
                    </div>
                  </div>
                )}
                {state && resources.events.slice(-3).map((event) => (
                  <div key={event.id} className="ff-agent-event">
                    <span className="ff-event-check"><CheckCircle2 aria-hidden="true" /></span>
                    <div><strong>{auditTitle(event)}</strong><p>{displayTime(event.created_at)} · {event.actor_id || '系统 Agent'}</p></div>
                  </div>
                ))}
              </details>
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

            {chatTool === 'newProject' && (
              <form onSubmit={onCreate} className="ff-form-card ff-chat-tool-card">
                <div className="ff-tool-card-header"><strong>生成新项目</strong><button type="button" onClick={() => setChatTool(null)} aria-label="关闭新项目表单"><X aria-hidden="true" /></button></div>
                <div className="ff-project-source-options" role="group" aria-label="新项目创建方式">
                  <button type="button" aria-pressed={projectSourceMode === 'describe'} onClick={() => setProjectSourceMode('describe')}>描述新需求</button>
                  <button type="button" aria-pressed={projectSourceMode === 'import'} onClick={() => setProjectSourceMode('import')}><Upload aria-hidden="true" />导入现有 PRD</button>
                </div>
                {projectSourceMode === 'import' && (
                  <div className="ff-prd-import-panel">
                    <p>原 PRD 会作为修订基线保留，Agent 将基于原文澄清、纠错并生成可继续编辑的新版本。</p>
                    <label className="ff-field">
                      <span>选择 PRD 文件（DOCX、Markdown、TXT 或文字型 PDF）</span>
                      <input
                        className="ff-field-control"
                        type="file"
                        accept=".docx,.md,.txt,.pdf"
                        aria-label="选择现有 PRD 文件"
                        disabled={importingPrd}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          event.target.value = '';
                          if (file) void onImportPrdFile(file);
                        }}
                      />
                    </label>
                    {importingPrd && <span className="ff-prd-import-status"><Loader2 className="ff-spin" aria-hidden="true" />正在解析 PRD…</span>}
                    <small>支持最多 30 万字符的提取文字；文件上限 24 MB，PDF 最多 50 页。</small>
                    {importedPrd && <span className="ff-prd-import-status is-ready"><CheckCircle2 aria-hidden="true" />已导入 {importedPrd.filename} · {importedPrd.content.length} 字</span>}
                    {importedPrd && importedPrd.content.length > 100_000 && <small role="status">大文档已完整导入；创建后的 Agent 分析可能较慢，建议按章节核对生成结果，确认前不要视为全文审核通过。</small>}
                  </div>
                )}
                <Field label={projectSourceMode === 'import' ? '希望如何修订' : '为什么要做'} value={motivation} onChange={setMotivation} multiline />
                <Field label={projectSourceMode === 'import' ? '修订后的最终目标' : '最终目标'} value={objective} onChange={setObjective} multiline />
                <Field label="已知范围（每行一项）" value={scope} onChange={setScope} multiline />
                <Field label="预期交付物（每行一项）" value={deliverables} onChange={setDeliverables} multiline />
                <Field label="负责人标识" value={ownerId} onChange={setOwnerId} />
                <button disabled={busy || projectsLoading || health !== 'ok' || importingPrd || (projectSourceMode === 'import' && !importedPrd)} className="ff-primary-button ff-full-button">
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
            {chatImages.length > 0 && <div className="ff-chat-attachments" aria-label="待发送截图">
              {chatImages.map((image, index) => <div key={`${image.file.name}:${index}`} className="ff-chat-attachment">
                <img src={image.preview} alt="待发送的 PRD 截图预览" />
                <button type="button" aria-label="移除截图" onClick={() => setChatImages(previous => previous.filter((_, position) => position !== index))}><X size={14} /></button>
              </div>)}
            </div>}
            {activeTab === 'prd' && prdChatContext && <details ref={prdChatDetailsRef} className="ff-prd-chat-context ff-prd-chat-context-collapsible" aria-label="当前 PRD 操作目标">
              <summary><strong>PRD 操作目标 · {prdChatContext.title}{prdChatContext.version ? ` · v${prdChatContext.version}` : ''}</strong><small>{prdChatContext.chapters.find(item => item.id === prdChatContext.selectedId)?.title ?? '当前章节'}{prdChatContext.discussion ? ` · ${prdChatContext.discussion.pinned ? '已固定' : '讨论中'}：${prdChatContext.discussion.label}` : ''}</small></summary>
              <button type="button" className="ff-prd-chat-context-collapse" aria-label="折叠 PRD 操作目标" title="折叠 PRD 操作目标" onClick={() => { if (prdChatDetailsRef.current) prdChatDetailsRef.current.open = false; }}><ChevronDown size={16} aria-hidden="true" /></button>
              <strong>{prdChatContext.title}{prdChatContext.version ? ` · v${prdChatContext.version}` : ''}</strong>
              <label>当前章节<select aria-label="选择 Agent 修改目标" value={prdChatContext.selectedId} onChange={event => prdEditorRef.current?.focusChapter(event.target.value)}>
                {prdChatContext.chapters.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
              </select></label>
              <small>{prdChatContext.selection ? `已选：${prdChatContext.selection}` : '默认按整篇全文执行；只有指令写明章节、在文档目录中勾选章节、或在此选择章节，才会限定修改范围。'}</small>
              {prdChatContext.discussion && <div aria-label="持续讨论对象"><strong>{prdChatContext.discussion.pinned ? '已固定 · ' : '讨论中 · '}{prdChatContext.discussion.label}</strong>
                <small>来源章节版本 {prdChatContext.discussion.version} · 本机保留</small>
                {prdChatContext.discussion.issue && <p role="alert">{prdChatContext.discussion.issue}</p>}
                <button type="button" onClick={() => prdEditorRef.current?.updateDiscussion('pin')}>{prdChatContext.discussion.pinned ? '取消固定' : '固定对象'}</button>
                <button type="button" onClick={() => prdEditorRef.current?.updateDiscussion('clear')}>移除对象与约束</button>
              </div>}
              <label>本对象的编辑约束<input maxLength={800} placeholder="例如：不改金额，保留业务编号" value={prdChatContext.discussion?.constraints ?? ''} onChange={e => prdEditorRef.current?.updateDiscussion('constraints', e.target.value)} /></label>
              <button type="button" onClick={() => prdEditorRef.current?.chooseObject()}>选择表格单元格 / 图中对象</button>
            </details>}
            {activeTab === 'prd' && prdEditReceipt && <section className="ff-prd-chat-context" aria-label="PRD 修改回执">
              <strong>{prdEditReceipt.version ? `已保存 v${prdEditReceipt.version}` : '修改已保存'} · 待手动审核</strong>
              <div>{prdEditReceipt.chapters.map(item => <button key={item.id} type="button" onClick={() => prdEditorRef.current?.focusChapter(item.id)}>{item.title}</button>)}</div>
              {prdEditReceipt.skipped > 0 && <small>未应用 {prdEditReceipt.skipped} 项操作</small>}
              <button type="button" onClick={() => setPrdEditReceipt(null)}>收起回执</button>
            </section>}
            <RecoveryNotice recovery={chatRecovery} onRestore={v => setChatDraft(v.text)} />
            {pendingInstructions.length > 0 && <section className="ff-pending-queue" aria-label="待执行指令队列">
              <header>
                <Clock size={14} aria-hidden="true" />
                <strong>待执行 · {pendingInstructions.length} 条</strong>
                <small>{agentBusy ? `当前：${agentBusyReason}` : '空闲，即将依次执行'}</small>
              </header>
              {queueNotice && <p className="ff-queue-notice" role="status">{queueNotice}</p>}
              <ol>
                {pendingInstructions.map((item, index) => <li key={item.id} className={index === 0 ? 'is-next' : undefined}>
                  <span className="ff-queue-order" aria-hidden="true">{index + 1}</span>
                  <span className="ff-queue-text" title={item.text}>{item.text}</span>
                  {item.images.length > 0 && <ImagePlus size={13} aria-hidden="true" />}
                  <button type="button" className="ff-queue-now" aria-label="立即生效" title="立即生效（可选择打断当前任务）" onClick={() => setQueuePrompt(item)}>
                    <ArrowUp size={15} aria-hidden="true" />
                  </button>
                  <button type="button" className="ff-queue-remove" aria-label="移除这条待执行指令" title="移除" onClick={() => removeInstruction(item.id)}>
                    <X size={14} aria-hidden="true" />
                  </button>
                </li>)}
              </ol>
            </section>}
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
                disabled={health !== 'ok'}
                onChange={(event) => setChatDraft(event.target.value)}
                onPaste={(event) => {
                  const files = Array.from(event.clipboardData.items)
                    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
                    .map(item => item.getAsFile()).filter((file): file is File => Boolean(file));
                  if (files.length) { event.preventDefault(); void addChatImages(files); }
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder={state ? activeTab === 'prd'
                  ? (agentBusy ? '当前任务执行中，发送后会排队自动执行…' : '例如：把项目目标部分补充验收标准；也可下达其他项目指令…')
                  : (agentBusy ? '当前任务执行中，发送后会排队自动执行…' : '输入全局指令、澄清答案或页面命令…')
                  : '直接描述新项目，或从左侧菜单打开完整表单…'}
                rows={1}
              />
              <div className="ff-chat-quick-actions">
                <button type="button" className="ff-chat-image-button" aria-label="添加 PRD 截图" title="添加 PRD 截图" disabled={health !== 'ok'} onClick={() => chatImageInputRef.current?.click()}>
                  <ImagePlus size={17} aria-hidden="true" />
                </button>
                {activeTab === 'prd' && <button type="button" className="ff-prd-tasks-icon-button" aria-label="查看 PRD 候选任务" title="查看 PRD 候选任务与保存的预览" onClick={() => prdEditorRef.current?.openTasks()}><ClipboardList size={17} aria-hidden="true" /></button>}
              </div>
              <input ref={chatImageInputRef} type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={health !== 'ok'} onChange={event => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = '';
                  if (files.length) void addChatImages(files);
                }} />
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
              <button type="submit" className={`ff-chat-send-button${agentBusy ? ' is-queued' : ''}`} disabled={!chatDraft.trim() || health !== 'ok'} aria-label="发送消息" title={agentBusy ? '当前任务执行中，发送后会排队自动执行' : '发送消息'}>
                {agentBusy ? <Clock aria-hidden="true" /> : <Send aria-hidden="true" />}
              </button>
            </form>
            {state && (
              <div className="ff-composer-actions ff-suggested-actions">
                <button type="button" className="ff-secondary-button" onClick={openPrdEditor}>编写 PRD</button>
                {sidebarActions.filter((action) => action !== 'message' && action !== 'create_spec').map((action) => (
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
          {queuePrompt && <div className="ff-queue-dialog-backdrop" role="presentation" onClick={() => setQueuePrompt(null)}>
            <div className="ff-queue-dialog" role="dialog" aria-modal="true" aria-label="立即生效确认" onClick={(event) => event.stopPropagation()}>
              <h2>让这条指令立刻生效？</h2>
              <p className="ff-queue-dialog-quote">{queuePrompt.text}</p>
              <p className="ff-queue-dialog-state">{agentBusy ? `当前正在执行：${agentBusyReason}` : '当前没有正在执行的任务，可直接执行。'}</p>
              <div className="ff-queue-dialog-actions">
                <button type="button" className="ff-primary-button" disabled={queueInterruptBusy} onClick={() => void applyQueuedInstructionNow(queuePrompt)}>
                  {queueInterruptBusy && <Loader2 className="ff-spin" aria-hidden="true" />}取消当前任务并立即执行
                </button>
                <button type="button" className="ff-secondary-button" disabled={queueInterruptBusy} onClick={() => {
                  promoteInstruction(queuePrompt.id);
                  setQueueNotice('已排到队首，当前任务结束后会立即执行。');
                  setQueuePrompt(null);
                  setQueueTick((tick) => tick + 1);
                }}>不打断，排到队首</button>
                <button type="button" className="ff-secondary-button" onClick={() => setQueuePrompt(null)}>关闭</button>
              </div>
            </div>
          </div>}
        </aside>
      )}

      <main className={`ff-workspace${activeTab === 'prd' ? ' is-prd-open' : ''}`}>
        {activeTab !== 'prd' && <header className="ff-top-tabs">
          <div className="ff-tabs">
            <button className={activeTab === 'dashboard' ? 'is-active' : ''} onClick={() => navigateToTab('dashboard')}>
              <House aria-hidden="true" />
              Dashboard
              <span>{dashboardProjects.length}</span>
            </button>
            <button className={activeTab === 'topLevel' ? 'is-active' : ''} onClick={() => navigateToTab('topLevel')}>
              <GitFork aria-hidden="true" />
              顶层图
              <span>{lifecycleDecision?.selected_model ? 'SDLC' : '—'}</span>
            </button>
            <button className={activeTab === 'kanban' ? 'is-active' : ''} onClick={() => navigateToTab('kanban')}>
              <LayoutGrid aria-hidden="true" />
              Kanban Board (敏捷看板)
              <span>{allResources.workItems.length}</span>
            </button>
            <button className={activeTab === 'flow' ? 'is-active' : ''} onClick={() => navigateToTab('flow')}>
              <GitFork aria-hidden="true" />
              DAG Flow Map (任务流转图)
            </button>
            <button className={activeTab === 'audit' ? 'is-active' : ''} onClick={() => navigateToTab('audit')}>
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
        </header>}

        {activeTab === 'prd' && state && <section className="ff-prd-workspace" aria-label="PRD 编写工作区">
          <Suspense fallback={<div className="prd-loading">正在加载 Univer 编辑器…</div>}>
            <EmbeddedPRDEditor
              ref={prdEditorRef}
              sessionId={state.session_id}
              embedded
              onClose={() => setActiveTab('topLevel')}
              onAgentProgress={handlePrdAgentProgress}
              onChatContext={setPrdChatContext}
              onChatEditApplied={(summary: string, receipt?: PrdEditReceipt) => {
                setPrdEditReceipt(receipt ?? null);
                appendChatMessage(state.session_id, 'agent', `已应用修改：${summary}${receipt?.version ? `\n全文版本 v${receipt.version}` : ''}${receipt?.chapters.length ? `\n修改位置：${receipt.chapters.map(item => item.title).join('、')}` : ''}${receipt?.skipped ? `\n未应用 ${receipt.skipped} 项操作` : ''}`, '已保存为新版本，等待手动审核。');
              }}
            />
          </Suspense>
        </section>}

        <section className={activeTab === 'dashboard' ? 'ff-tab-pane is-active' : 'ff-tab-pane'} aria-hidden={activeTab !== 'dashboard'}>
          <WorkspaceDashboard
            projects={dashboardProjects}
            currentUserId={CURRENT_ACTOR_ID}
            loading={projectsLoading}
            onRefresh={() => { void refreshAllProjects(); }}
            onOpenWorkspace={openSharedWorkspace}
            onViewAllProjects={() => setActiveTab('kanban')}
            onOpenProject={(sessionId) => {
              if (sessionId !== activeSessionId) switchConversation(sessionId);
              setActiveTab('topLevel');
            }}
            onOpenWorkItem={(sessionId, workItemId) => {
              if (sessionId !== activeSessionId) switchConversation(sessionId);
              setSelectedWorkItemId(workItemId);
              setActiveTab('kanban');
            }}
          />
        </section>

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
            <button
              type="button"
              className="ff-secondary-button"
              onClick={openPrdEditor}
              disabled={!state}
              title="查看当前任务的 PRD（等同点击「编写 PRD」）"
            >
              <FileText aria-hidden="true" />查看 PRD
            </button>
            <button
              type="button"
              className="ff-secondary-button"
              onClick={() => void openPrototype()}
              disabled={!currentProject || prototypeBusy}
              title="在新标签页打开当前项目的 HTML 原型；PRD 未定稿时可基于当前草稿生成（草稿版）"
            >
              {prototypeBusy
                ? <Loader2 className="ff-spin" aria-hidden="true" />
                : <ExternalLink aria-hidden="true" />}查看原型
            </button>
            <button type="button" className="ff-secondary-button" disabled={!currentProject || presentationBusy} onClick={() => void createPrdPresentation('team')} title="制作团队讲解版 PPT 并打开在线预览页（页内可下载）；PRD 未定稿时可基于当前草稿生成（草稿版）">
              {presentationBusy ? <Loader2 className="ff-spin" aria-hidden="true" /> : <Presentation aria-hidden="true" />}团队 PPT
            </button>
            <button type="button" className="ff-secondary-button" disabled={!currentProject || presentationBusy} onClick={() => void createPrdPresentation('external')} title="制作对外介绍版 PPT 并打开在线预览页（页内可下载）；PRD 未定稿时可基于当前草稿生成（草稿版）">
              {presentationBusy ? <Loader2 className="ff-spin" aria-hidden="true" /> : <Presentation aria-hidden="true" />}对外 PPT
            </button>
            <button onClick={() => void refreshAllProjects()} disabled={busy || projectsLoading} className="ff-secondary-button">
              <RefreshCw className={projectsLoading ? 'ff-spin' : ''} aria-hidden="true" />刷新
            </button>
          </div>
          {prototypeNotice && (
            <div className={`ff-top-level-notice is-${prototypeNotice.tone}`} role="status" aria-live="polite">
              <span>{prototypeNotice.text}</span>
              <button type="button" aria-label="关闭提示" onClick={() => setPrototypeNotice(null)}>
                <X aria-hidden="true" />
              </button>
            </div>
          )}
          {prototypePrompt && (
            <div className="ff-queue-dialog-backdrop" role="presentation" onClick={() => { if (!prototypeBusy) setPrototypePrompt(null); }}>
              <div className="ff-queue-dialog" role="dialog" aria-modal="true" aria-label="基于当前 PRD 草稿生成原型" onClick={(event) => event.stopPropagation()}>
                <h2>基于当前 PRD 草稿生成 HTML 原型？</h2>
                <p className="ff-queue-dialog-state">当前任务的 PRD 尚未定稿（草稿第 {prototypePrompt.revision} 版）。确认后将立即基于这份草稿生成一版 HTML 原型草稿，用于提前预览；这不等于「进入下一阶段」，也不会改动 PRD 正文或版本。</p>
                <div className="ff-queue-dialog-actions">
                  <button type="button" className="ff-primary-button" disabled={prototypeBusy} onClick={() => void confirmDraftPrototype()}>
                    {prototypeBusy && <Loader2 className="ff-spin" aria-hidden="true" />}确认生成原型草稿
                  </button>
                  <button type="button" className="ff-secondary-button" disabled={prototypeBusy} onClick={() => setPrototypePrompt(null)}>取消</button>
                </div>
              </div>
            </div>
          )}
          {presentationPrompt && (
            <div className="ff-queue-dialog-backdrop" role="presentation" onClick={() => { if (!presentationBusy) setPresentationPrompt(null); }}>
              <div className="ff-queue-dialog" role="dialog" aria-modal="true" aria-label="基于当前 PRD 草稿生成 PPT" onClick={(event) => event.stopPropagation()}>
                <h2>基于当前 PRD 草稿生成{presentationPrompt.audience === 'team' ? '团队' : '对外'} PPT？</h2>
                <p className="ff-queue-dialog-state">当前任务的 PRD 尚未定稿（草稿第 {presentationPrompt.revision} 版）。确认后将走与正式版一致的编排流程，基于这份草稿生成一版可编辑 PPTX（首页会标注「草稿」），并在新标签页打开在线预览页，可在其中下载；这不等于「进入下一阶段」，也不会改动 PRD 正文或版本。</p>
                <div className="ff-queue-dialog-actions">
                  <button type="button" className="ff-primary-button" disabled={presentationBusy} onClick={() => void confirmDraftPresentation()}>
                    {presentationBusy && <Loader2 className="ff-spin" aria-hidden="true" />}确认生成并预览
                  </button>
                  <button type="button" className="ff-secondary-button" disabled={presentationBusy} onClick={() => setPresentationPrompt(null)}>取消</button>
                </div>
              </div>
            </div>
          )}
          <div className="ff-top-level-canvas">
            {currentProject ? (
              <TopLevelGraph
                projectTitle={projectTitle(currentProject)}
                agentSpecs={currentProject.resources.agentSpecs}
                specs={currentProject.resources.specs}
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

        {activeTab !== 'prd' && <footer className="ff-status-bar">
          <div><span className="ff-small-dot" /> AI STUDIO ENGINE <span>UTF-8</span><span>Backend Contract</span></div>
          <div><span className={health === 'ok' ? 'ff-small-dot' : 'ff-small-dot is-error'} /> AGENT NETWORK {health === 'ok' ? 'ONLINE' : 'OFFLINE'} <span>{allResources.agentSpecs.length} SPECS</span></div>
        </footer>}
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

      {showSettings ? (
        <SettingsPanel
          onClose={() => setShowSettings(false)}
          onLogout={() => {
            clearSession();
            window.location.reload();
          }}
          onOpenAdmin={() => {
            setShowSettings(false);
            setShowAdminModal(true);
          }}
        />
      ) : null}

      {showAdminModal ? <AdminUsersPage onClose={() => setShowAdminModal(false)} /> : null}
    </div>
  );
}
