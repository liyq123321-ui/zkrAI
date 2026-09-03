import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2, RefreshCw, Server, ShieldCheck } from 'lucide-react';
import { apiClient } from './client';
import { appConfig } from './config';
import type {
  AgentSpecDto,
  AuditEventDto,
  CommandAction,
  HealthDto,
  ProjectBriefDto,
  SessionStateDto,
  SpecVersionDto,
  WorkItemDto,
} from './dto';
import { ApiError, normalizeNetworkError } from './errors';
import { PrdReviewPanel } from './PrdReviewPanel';
import {
  createSession,
  executeCommand,
  getSessionState,
  listAgentSpecs,
  listEvents,
  listSpecs,
  listWorkItems,
} from './sessions';
import {
  actionPlacement,
  isSkipClarificationIntent,
  normalizeSessionId,
  workItemPresentation,
} from './workflowUi';

const SESSION_KEY = 'firstflight.active-session-id';
const actionLabels: Record<CommandAction, string> = {
  message: '提交澄清',
  skip_clarification: '跳过澄清并生成 PRD',
  create_spec: '生成 Spec',
  revise: '生成修订版',
  approve: '人工通过',
  reject: '人工驳回',
  rework: '要求返工',
  publish_review: '发布审核',
  convert_to_work_item: '拆解 WorkItem',
  restore_spec_version: '基于历史版本创建新版',
};

type ResourceBundle = {
  specs: SpecVersionDto[];
  workItems: WorkItemDto[];
  agentSpecs: AgentSpecDto[];
  events: AuditEventDto[];
};

const emptyResources: ResourceBundle = { specs: [], workItems: [], agentSpecs: [], events: [] };

function lines(value: string): string[] {
  return value.split('\n').map((item) => item.trim()).filter(Boolean);
}

function errorText(error: unknown): string {
  const normalized = normalizeNetworkError(error);
  return `${normalized.code}：${normalized.message}`;
}

function Field({ label, value, onChange, multiline = false }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
}) {
  const className = 'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500';
  return (
    <label className="grid gap-1 text-sm text-slate-300">
      <span>{label}</span>
      {multiline ? (
        <textarea className={`${className} min-h-24 resize-y`} value={value} onChange={(event) => onChange(event.target.value)} />
      ) : (
        <input className={className} value={value} onChange={(event) => onChange(event.target.value)} />
      )}
    </label>
  );
}

function StatusBadge({ state }: { state: SessionStateDto }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="rounded-full bg-cyan-500/15 px-3 py-1 text-cyan-300">{state.phase}</span>
      {state.current_spec_status && <span className="rounded-full bg-violet-500/15 px-3 py-1 text-violet-300">{state.current_spec_status}</span>}
      <span className="rounded-full bg-slate-800 px-3 py-1 text-slate-300">状态版本 {state.state_version}</span>
    </div>
  );
}

export function ApiWorkspace() {
  const [health, setHealth] = useState<'checking' | 'ok' | 'error'>('checking');
  const [state, setState] = useState<SessionStateDto | null>(null);
  const [resources, setResources] = useState<ResourceBundle>(emptyResources);
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
  const [workflowProgress, setWorkflowProgress] = useState<string | null>(null);
  const pendingCommandIds = useRef(new Map<string, string>());

  const refreshResources = useCallback(async (sessionId: string, signal?: AbortSignal) => {
    const [nextState, specs, workItems, agentSpecs, events] = await Promise.all([
      getSessionState(sessionId, signal),
      listSpecs(sessionId, signal),
      listWorkItems(sessionId, signal),
      listAgentSpecs(sessionId, signal),
      listEvents(sessionId, signal),
    ]);
    const bundle = { specs, workItems, agentSpecs, events };
    setState(nextState);
    setResources(bundle);
    return { state: nextState, resources: bundle };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    apiClient.request<HealthDto>('/healthz', { signal: controller.signal })
      .then(() => setHealth('ok'))
      .catch((reason) => {
        if ((reason as ApiError).code !== 'REQUEST_ABORTED') {
          setHealth('error');
          setError(errorText(reason));
        }
      });

    const savedSessionId = localStorage.getItem(SESSION_KEY);
    if (savedSessionId) {
      refreshResources(savedSessionId, controller.signal).catch((reason) => {
        if ((reason as ApiError).code !== 'REQUEST_ABORTED') setError(errorText(reason));
      });
    }
    return () => controller.abort();
  }, [refreshResources]);

  const currentSpec = useMemo(
    () => [...resources.specs].sort((left, right) => right.revision - left.revision)[0],
    [resources.specs],
  );
  const rootWorkItem = useMemo(
    () => resources.workItems.find((item) => item.kind === 'ROOT'),
    [resources.workItems],
  );
  const selectedWorkItem = useMemo(
    () => resources.workItems.find((item) => item.id === selectedWorkItemId) ?? null,
    [resources.workItems, selectedWorkItemId],
  );
  const selectedAgentSpecs = useMemo(
    () => resources.agentSpecs.filter((item) => item.work_item_id === selectedWorkItemId),
    [resources.agentSpecs, selectedWorkItemId],
  );
  const placedActions = useMemo(
    () => actionPlacement(state?.legal_actions ?? []),
    [state?.legal_actions],
  );

  async function onCreate(event: FormEvent) {
    event.preventDefault();
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
    try {
      const created = await createSession(crypto.randomUUID(), brief);
      localStorage.setItem(SESSION_KEY, created.session_id);
      setState(created);
      await refreshResources(created.session_id);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function resumeExistingSession() {
    setBusy(true);
    setError(null);
    try {
      const sessionId = normalizeSessionId(resumeSessionId);
      const refreshed = await refreshResources(sessionId);
      localStorage.setItem(SESSION_KEY, sessionId);
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
    pendingCommandIds.current.delete(fingerprint);
    setState(result.state);
    return result.state;
  }

  async function runAction(action: CommandAction) {
    if (!state) return;
    const effectiveAction = action === 'message'
      && state.legal_actions.includes('skip_clarification')
      && isSkipClarificationIntent(answer)
      ? 'skip_clarification'
      : action;
    const message = effectiveAction === 'message'
      ? answer
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
      if (
        effectiveAction === 'skip_clarification'
        && nextState.legal_actions.includes('create_spec')
      ) {
        setWorkflowProgress('正在根据现有信息和合理假设生成 PRD…');
        nextState = await submitCommand(nextState, 'create_spec');
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
        setError('状态已被其他操作更新。页面已刷新，请确认最新状态后重新提交。');
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

  async function confirmPrdAndDecompose(reviewNote: string) {
    if (!state) return;
    setBusy(true);
    setError(null);
    let nextState = state;
    try {
      if (nextState.legal_actions.includes('approve')) {
        setWorkflowProgress('正在确认当前 PRD…');
        nextState = await submitCommand(nextState, 'approve', reviewNote || undefined);
      }
      if (nextState.legal_actions.includes('convert_to_work_item')) {
        setWorkflowProgress('PRD 已确认，正在拆解子 WorkItem 和 Agent Spec…');
        nextState = await submitCommand(nextState, 'convert_to_work_item');
      }
      const refreshed = await refreshResources(nextState.session_id);
      const firstTask = refreshed.resources.workItems.find((item) => item.kind === 'TASK');
      if (firstTask) setSelectedWorkItemId(firstTask.id);
    } catch (reason) {
      const normalized = normalizeNetworkError(reason);
      if (normalized.status === 409 && normalized.code === 'STALE_STATE') {
        pendingCommandIds.current.clear();
        await refreshResources(state.session_id).catch(() => undefined);
        setError('状态已被其他操作更新。页面已刷新，请确认最新 PRD 后重新提交。');
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

  const refreshCurrentResources = useCallback(async () => {
    if (state) await refreshResources(state.session_id);
  }, [refreshResources, state?.session_id]);

  function clearSession() {
    localStorage.removeItem(SESSION_KEY);
    setState(null);
    setResources(emptyResources);
    setSelectedWorkItemId(null);
    setError(null);
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-950/95 px-5 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-cyan-400" /><h1 className="font-semibold">firstFlight 工作台</h1></div>
            <p className="mt-1 text-xs text-slate-400">真实 API 模式 · 不使用 Mock 业务数据</p>
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-400">
            <Server className="h-4 w-4" />
            <span>{appConfig.apiBaseUrl}</span>
            <span className={health === 'ok' ? 'text-emerald-400' : health === 'error' ? 'text-rose-400' : 'text-amber-300'}>
              {health === 'ok' ? '后端已连接' : health === 'error' ? '后端不可用' : '正在检查'}
            </span>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl gap-5 p-5 lg:grid-cols-[360px_1fr]">
        <aside className="space-y-4">
          {!state ? (
            <form onSubmit={onCreate} className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <div><h2 className="font-medium">创建项目 Session</h2><p className="mt-1 text-xs text-slate-400">填写最小项目简报，由后端 PM Agent 判断是否需要澄清。</p></div>
              <Field label="为什么要做" value={motivation} onChange={setMotivation} multiline />
              <Field label="最终目标" value={objective} onChange={setObjective} multiline />
              <Field label="已知范围（每行一项）" value={scope} onChange={setScope} multiline />
              <Field label="预期交付物（每行一项）" value={deliverables} onChange={setDeliverables} multiline />
              <Field label="负责人标识（需与后端本地身份一致）" value={ownerId} onChange={setOwnerId} />
              <button disabled={busy || health !== 'ok'} className="flex w-full items-center justify-center gap-2 rounded-lg bg-cyan-500 px-4 py-2 font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-50">
                {busy && <Loader2 className="h-4 w-4 animate-spin" />} 创建并分析
              </button>
              <div className="border-t border-slate-800 pt-4">
                <p className="mb-3 text-xs leading-5 text-slate-400">已有后端 Session 时可直接恢复，不会创建新的 Agent 运行。</p>
                <Field label="已有 Session ID" value={resumeSessionId} onChange={setResumeSessionId} />
                <button type="button" disabled={busy || health !== 'ok'} onClick={resumeExistingSession} className="mt-3 w-full rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-4 py-2 text-sm text-cyan-200 disabled:opacity-50">恢复并打开</button>
              </div>
            </form>
          ) : (
            <section className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <div className="flex items-center justify-between"><h2 className="font-medium">当前 Session</h2><button onClick={manualRefresh} disabled={busy} title="刷新"><RefreshCw className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`} /></button></div>
              <StatusBadge state={state} />
              <dl className="grid gap-2 text-xs text-slate-400"><div><dt>Session</dt><dd className="break-all text-slate-200">{state.session_id}</dd></div><div><dt>下一步</dt><dd className="text-slate-200">{state.next_action}</dd></div></dl>

              {state.outstanding_questions.length > 0 && <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3"><h3 className="text-sm font-medium text-amber-200">待澄清</h3>{state.outstanding_questions.map((question) => <div key={question.question_id} className="mt-3 text-sm"><p>{question.question}</p><p className="mt-1 text-xs text-slate-400">{question.reason}</p></div>)}</div>}
              {state.legal_actions.includes('message') && <Field label="澄清答案" value={answer} onChange={setAnswer} multiline />}
              {state.legal_actions.includes('restore_spec_version') && <div className="space-y-2 rounded-xl border border-violet-500/20 bg-violet-500/5 p-3"><p className="text-xs text-violet-200">历史不会被覆盖；系统会复制所选内容并创建新的 n+1 版本，重新进入审核。</p><select value={restoreRevision} onChange={(event) => setRestoreRevision(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"><option value="">选择历史版本</option>{resources.specs.filter((spec) => spec.id !== state.current_spec_version_id).map((spec) => <option key={spec.id} value={spec.revision}>v{spec.revision} · {spec.status}</option>)}</select><Field label="创建新版的原因" value={restoreReason} onChange={setRestoreReason} multiline /></div>}
              <div className="grid gap-2">
                {placedActions.sidebar.map((action) => <button key={action} disabled={busy} onClick={() => runAction(action)} className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-sm text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50">{actionLabels[action]}</button>)}
              </div>
              {placedActions.prd.length > 0 && <p className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs leading-5 text-slate-400">PRD 审核和任务拆解已收拢到主 WorkItem 卡片，避免绕过 Gitea 人工审核。</p>}
              <button onClick={clearSession} className="w-full text-xs text-slate-500 hover:text-slate-300">关闭本地 Session 记录</button>
            </section>
          )}
        </aside>

        <section className="space-y-5">
          {error && <div className="flex gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200"><AlertCircle className="h-5 w-5 shrink-0" /><span>{error}</span></div>}
          {workflowProgress && <div className="flex items-center gap-3 rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-4 text-sm text-cyan-100"><Loader2 className="h-5 w-5 animate-spin" /><span>{workflowProgress}</span></div>}
          {!state ? <div className="grid min-h-80 place-items-center rounded-2xl border border-dashed border-slate-800 text-sm text-slate-500">创建或恢复 Session 后，这里显示真实 Spec、WorkItem 和审计记录。</div> : <>
            <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><div><h2 className="font-medium">规划 Kanban</h2><p className="mt-1 text-xs text-slate-500">主卡进入 PRD 审核；子卡查看拆解后的 Agent Spec。所有卡片只读，不代表已执行。</p></div>{currentSpec && <span className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-300">PRD v{currentSpec.revision} · {currentSpec.status}</span>}</div>
              <div className="grid gap-4 xl:grid-cols-3">
                {([
                  { kind: 'ROOT', title: '主 WorkItem' },
                  { kind: 'MILESTONE', title: '里程碑' },
                  { kind: 'TASK', title: '子 WorkItem' },
                ] as const).map((column) => {
                  const items = resources.workItems.filter((item) => item.kind === column.kind);
                  return <div key={column.kind} className="rounded-xl border border-slate-800 bg-slate-950/70 p-3"><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-medium text-slate-200">{column.title}</h3><span className="text-xs text-slate-500">{items.length}</span></div><div className="space-y-3">{items.map((item) => {
                    const hasAgentSpec = resources.agentSpecs.some((spec) => spec.work_item_id === item.id);
                    const presentation = workItemPresentation(item.kind, hasAgentSpec);
                    const disabled = presentation.detailKind === 'prd' && !currentSpec;
                    return <button key={item.id} disabled={disabled} onClick={() => setSelectedWorkItemId(item.id)} className={`w-full rounded-xl border p-4 text-left transition ${selectedWorkItemId === item.id ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-800 bg-slate-950 hover:border-slate-600'} disabled:cursor-not-allowed disabled:opacity-50`}><div className="flex items-start justify-between gap-2"><h4 className="font-medium">{item.title || item.id}</h4><span className="text-[11px] text-cyan-300">{item.kind}</span></div><p className="mt-2 line-clamp-3 text-sm text-slate-400">{item.objective || item.description || '未提供目标'}</p><p className="mt-3 text-xs font-medium text-cyan-300">{disabled ? 'PRD 生成后可打开' : presentation.cardLabel}</p></button>;
                  })}{items.length === 0 && <p className="rounded-lg border border-dashed border-slate-800 p-4 text-xs text-slate-600">当前阶段尚无卡片</p>}</div></div>;
                })}
              </div>
            </section>
            {selectedWorkItem?.kind === 'ROOT' && rootWorkItem && currentSpec && <PrdReviewPanel wi={rootWorkItem.id} sessionState={state} workflowBusy={busy} onConfirmAndDecompose={confirmPrdAndDecompose} onResourcesChanged={refreshCurrentResources} />}
            {selectedWorkItem && selectedWorkItem.kind !== 'ROOT' && <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5"><div className="mb-4 flex flex-wrap items-start justify-between gap-2"><div><h2 className="font-medium">{selectedWorkItem.title || selectedWorkItem.id}</h2><p className="mt-1 text-xs text-slate-500">{selectedWorkItem.kind} · 负责人 {selectedWorkItem.suggested_assignee || selectedWorkItem.responsible_role || '未提供'}</p></div><span className="text-xs text-slate-500">依赖：{selectedWorkItem.dependency_work_item_ids.join(', ') || '无'}</span></div><p className="text-sm leading-6 text-slate-300">{selectedWorkItem.objective || selectedWorkItem.description || '未提供目标'}</p>{selectedAgentSpecs.length > 0 ? <div className="mt-4 space-y-3">{selectedAgentSpecs.map((agentSpec) => <div key={agentSpec.id} className="rounded-xl border border-cyan-500/20 bg-slate-950 p-4"><h3 className="text-sm font-medium text-cyan-200">任务 Agent Spec</h3><pre className="mt-3 max-h-[520px] overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-300">{JSON.stringify(agentSpec.content, null, 2)}</pre></div>)}</div> : <p className="mt-4 rounded-lg border border-dashed border-slate-800 p-4 text-sm text-slate-500">该卡片当前没有 Agent Spec。</p>}</section>}
            <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5"><div className="mb-4 flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-emerald-400" /><h2 className="font-medium">安全审计摘要</h2></div><ol className="space-y-3">{resources.events.slice().reverse().slice(0, 30).map((event) => <li key={event.id} className="border-l border-slate-700 pl-4"><p className="text-sm text-slate-200">{event.event_type === 'AGENT_TRACE' ? String(event.payload.summary || event.payload.phase) : event.event_type}</p>{event.event_type === 'AGENT_TRACE' && <p className="text-xs text-cyan-300">{String(event.payload.status)} · {String(event.payload.phase)}</p>}<p className="text-xs text-slate-500">{new Date(event.created_at).toLocaleString()} · {event.actor_id || 'system'}</p></li>)}</ol></section>
          </>}
        </section>
      </div>
    </main>
  );
}
