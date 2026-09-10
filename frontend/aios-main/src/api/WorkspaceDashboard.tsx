import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  BriefcaseBusiness,
  CheckCircle2,
  CircleUserRound,
  Clock3,
  FileCheck2,
  FolderKanban,
  Gauge,
  Loader2,
  RefreshCw,
  Sparkles,
  Zap,
} from 'lucide-react';
import type { AgentRuntimeDto, AgentRuntimeEventDto, AuditEventDto, WorkItemDto } from './dto';
import { listAgentRuntime, listAgentRuntimeEvents } from './sessions';
import { displayLabel, displayTime, auditTitle } from './presentation';
import { projectSpec, projectTitle, type WorkspaceProject } from './useWorkspaceProjects';
import { workItemLane, workItemStatusLabel } from './workflowUi';

type ProjectTelemetry = {
  runtimes: AgentRuntimeDto[];
  events: AgentRuntimeEventDto[];
};

type DashboardTodo = {
  id: string;
  sessionId: string;
  workItemId: string;
  kind: 'review' | 'approval' | 'task' | 'issue';
  title: string;
  project: string;
  detail: string;
  urgent: boolean;
};

const todoLabels: Record<DashboardTodo['kind'], string> = {
  review: '审查',
  approval: '签审',
  task: '任务',
  issue: '处理',
};

function number(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function usage(events: AgentRuntimeEventDto[]) {
  return events.reduce((total, event) => {
    if (event.event_type !== 'turn.completed') return total;
    const value = event.payload.usage;
    if (!value || typeof value !== 'object' || Array.isArray(value)) return total;
    const record = value as Record<string, unknown>;
    return {
      input: total.input + number(record.input_tokens),
      output: total.output + number(record.output_tokens),
    };
  }, { input: 0, output: 0 });
}

function formatCount(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value);
}

function projectStage(project: WorkspaceProject): string {
  const milestones = project.resources.workItems.filter((item) => item.kind === 'MILESTONE');
  const active = milestones.find((item) => workItemLane(item.status) === 'in_progress')
    ?? milestones.find((item) => workItemLane(item.status) !== 'done');
  return active?.title || displayLabel(project.state.phase);
}

function projectProgress(project: WorkspaceProject): number {
  const tasks = project.resources.workItems.filter((item) => item.kind === 'TASK');
  if (!tasks.length) return project.state.phase === 'AGENT_SPECS_READY' ? 100 : 0;
  return Math.round(tasks.filter((item) => workItemLane(item.status) === 'done').length / tasks.length * 100);
}

function projectStatus(project: WorkspaceProject): { label: string; tone: string } {
  if (project.state.review_findings.length > 0 || project.state.current_spec_status === 'REWORK') {
    return { label: '需要处理', tone: 'warning' };
  }
  if (project.state.phase === 'AGENT_SPECS_READY') return { label: '执行就绪', tone: 'success' };
  if (project.state.phase === 'REVIEW' || project.state.current_spec_status === 'HUMAN_REVIEW') {
    return { label: '等待确认', tone: 'attention' };
  }
  return { label: '进行中', tone: 'active' };
}

function eventTone(event: AuditEventDto): 'error' | 'success' | 'info' {
  const code = String(event.payload.safe_error_code ?? event.payload.error_code ?? '');
  if (/FAILED|ERROR|UNAVAILABLE/i.test(`${event.event_type} ${code}`)) return 'error';
  if (/COMPLETED|APPROVED|READY|CONVERTED/i.test(event.event_type)) return 'success';
  return 'info';
}

function eventDetail(event: AuditEventDto): string {
  const detail = event.payload.message ?? event.payload.summary ?? event.payload.status;
  return detail ? String(detail) : event.actor_id ? `由 ${event.actor_id} 更新` : '系统状态已更新';
}

export function WorkspaceDashboard({
  projects,
  currentUserId,
  loading,
  onRefresh,
  onViewAllProjects,
  onOpenProject,
  onOpenWorkItem,
}: {
  projects: WorkspaceProject[];
  currentUserId: string;
  loading: boolean;
  onRefresh: () => void;
  onViewAllProjects: () => void;
  onOpenProject: (sessionId: string) => void;
  onOpenWorkItem: (sessionId: string, workItemId: string) => void;
}) {
  const [telemetry, setTelemetry] = useState<Record<string, ProjectTelemetry>>({});
  const [telemetryLoading, setTelemetryLoading] = useState(false);
  const sessionIds = projects.map((project) => project.state.session_id).sort().join(',');

  const refreshTelemetry = useCallback(async (signal?: AbortSignal) => {
    if (!projects.length) {
      setTelemetry({});
      return;
    }
    setTelemetryLoading(true);
    try {
      const settled = await Promise.allSettled(projects.map(async (project) => {
        const sessionId = project.state.session_id;
        const runtimes = await listAgentRuntime(sessionId, signal);
        const runtimeEvents = await Promise.allSettled(runtimes.map((runtime) =>
          listAgentRuntimeEvents(sessionId, runtime.agent_session_id, signal)));
        const events = runtimeEvents.flatMap((result) => result.status === 'fulfilled' ? result.value : []);
        return [sessionId, { runtimes, events }] as const;
      }));
      if (signal?.aborted) return;
      setTelemetry(Object.fromEntries(settled.flatMap((result) =>
        result.status === 'fulfilled' ? [result.value] : [])));
    } finally {
      if (!signal?.aborted) setTelemetryLoading(false);
    }
  }, [sessionIds]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshTelemetry(controller.signal);
    return () => controller.abort();
  }, [refreshTelemetry]);

  const allRuntime = useMemo(() => Object.values(telemetry).flatMap((item) => item.runtimes), [telemetry]);
  const allRuntimeEvents = useMemo(() => Object.values(telemetry).flatMap((item) => item.events), [telemetry]);
  const tokenUsage = useMemo(() => usage(allRuntimeEvents), [allRuntimeEvents]);
  const tasks = useMemo(() => projects.flatMap((project) => project.resources.workItems)
    .filter((item) => item.kind === 'TASK'), [projects]);
  const activeProjects = projects.filter((project) => projectProgress(project) < 100).length;
  const runningAgents = allRuntime.filter((runtime) => runtime.status === 'running').length;

  const todos = useMemo<DashboardTodo[]>(() => projects.flatMap((project) => {
    const name = projectTitle(project);
    const root = project.resources.workItems.find((item) => item.kind === 'ROOT');
    const spec = projectSpec(project);
    const items: DashboardTodo[] = [];
    const assignedToUser = (item: WorkItemDto) => item.suggested_assignee === currentUserId;
    if (root && assignedToUser(root) && spec && (
      project.state.legal_actions.includes('approve')
      || project.state.legal_actions.includes('publish_review')
      || project.state.current_spec_status === 'HUMAN_REVIEW'
      || project.state.current_spec_status === 'REWORK'
    )) {
      items.push({
        id: `${project.state.session_id}:review`, sessionId: project.state.session_id, workItemId: root.id,
        kind: project.state.review_findings.length ? 'issue' : 'approval',
        title: project.state.review_findings.length
          ? `PRD v${spec.revision} 有 ${project.state.review_findings.length} 项审核问题`
          : `PRD v${spec.revision} 等待签字确认`,
        project: name,
        detail: project.state.review_findings.length ? '请核对审核意见并安排修订' : '请阅读版本差异和审核意见',
        urgent: project.state.review_findings.length > 0,
      });
    }
    for (const item of project.resources.workItems.filter((candidate) =>
      candidate.kind !== 'ROOT' && assignedToUser(candidate) && workItemLane(candidate.status) !== 'done')) {
      items.push({
        id: `${project.state.session_id}:${item.id}`, sessionId: project.state.session_id, workItemId: item.id,
        kind: /review|审核|验收|签字/i.test(`${item.title} ${item.responsible_role}`) ? 'review' : 'task',
        title: item.title || '未命名任务', project: name,
        detail: `${projectStage(project)} · ${workItemStatusLabel(item.status)}`,
        urgent: workItemLane(item.status) === 'failed' || item.status?.toLowerCase() === 'blocked',
      });
    }
    return items;
  }).sort((left, right) => Number(right.urgent) - Number(left.urgent)), [currentUserId, projects]);

  const notices = useMemo(() => projects.flatMap((project) => project.resources.events.map((event) => ({
    event,
    project: projectTitle(project),
    sessionId: project.state.session_id,
  }))).sort((left, right) => right.event.created_at.localeCompare(left.event.created_at)).slice(0, 8), [projects]);

  return (
    <div className="ff-dashboard-scroll">
      <div className="ff-dashboard">
        <header className="ff-dashboard-hero">
          <div className="ff-dashboard-user">
            <span><CircleUserRound aria-hidden="true" /></span>
            <div><small>PROJECT COMMAND CENTER</small><h1>早上好，{currentUserId}</h1><p>Project Manager · 今天有 {todos.length} 项待办，{activeProjects} 个项目正在推进</p></div>
          </div>
          <button type="button" onClick={() => { onRefresh(); void refreshTelemetry(); }} disabled={loading || telemetryLoading}>
            <RefreshCw className={loading || telemetryLoading ? 'ff-spin' : ''} aria-hidden="true" />刷新总览
          </button>
        </header>

        <section className="ff-dashboard-metrics" aria-label="用户总览统计">
          <article><span className="is-blue"><Zap aria-hidden="true" /></span><div><small>累计 Token</small><strong>{formatCount(tokenUsage.input + tokenUsage.output)}</strong><p>输入 {formatCount(tokenUsage.input)} · 输出 {formatCount(tokenUsage.output)}</p></div></article>
          <article><span className="is-green"><Bot aria-hidden="true" /></span><div><small>已启动 Agent</small><strong>{formatCount(allRuntime.length)}</strong><p>{runningAgents} 个正在运行</p></div></article>
          <article><span className="is-violet"><FolderKanban aria-hidden="true" /></span><div><small>进行中项目</small><strong>{activeProjects}</strong><p>名下共 {projects.length} 个项目</p></div></article>
          <article><span className="is-amber"><FileCheck2 aria-hidden="true" /></span><div><small>我的待办</small><strong>{todos.length}</strong><p>{todos.filter((todo) => todo.urgent).length} 项需要优先处理</p></div></article>
        </section>

        <div className="ff-dashboard-grid">
          <section className="ff-dashboard-panel ff-dashboard-todos">
            <header><div><small>MY WORK</small><h2>我的待办 <span>{todos.length}</span></h2></div><Clock3 aria-hidden="true" /></header>
            <div className="ff-dashboard-todo-list">
              {todos.length ? todos.slice(0, 6).map((todo) => (
                <button type="button" key={todo.id} className={todo.urgent ? 'is-urgent' : ''} onClick={() => onOpenWorkItem(todo.sessionId, todo.workItemId)}>
                  <span className={`ff-dashboard-todo-kind is-${todo.kind}`}>{todoLabels[todo.kind]}</span>
                  <span className="ff-dashboard-todo-copy"><strong>{todo.title}</strong><small>{todo.project} · {todo.detail}</small></span>
                  <span className="ff-dashboard-todo-action">去处理<ArrowRight aria-hidden="true" /></span>
                </button>
              )) : <div className="ff-dashboard-empty"><CheckCircle2 aria-hidden="true" /><strong>当前没有待办</strong><p>分配给 {currentUserId} 的任务与审核项会显示在这里。</p></div>}
            </div>
          </section>

          <aside className="ff-dashboard-panel ff-dashboard-pulse">
            <header><div><small>LIVE PULSE</small><h2>运行脉搏</h2></div><Gauge aria-hidden="true" /></header>
            <dl>
              <div><dt>任务完成</dt><dd>{tasks.filter((item) => workItemLane(item.status) === 'done').length}<small> / {tasks.length}</small></dd></div>
              <div><dt>Agent 异常</dt><dd className={allRuntime.some((runtime) => runtime.status === 'error') ? 'is-danger' : ''}>{allRuntime.filter((runtime) => runtime.status === 'error').length}</dd></div>
              <div><dt>等待确认</dt><dd>{projects.filter((project) => project.state.phase === 'REVIEW' || project.state.current_spec_status === 'HUMAN_REVIEW').length}</dd></div>
            </dl>
            <div className="ff-dashboard-pulse-line"><i /><i /><i /><i /><i /><i /><i /></div>
            <p><span />后台服务{runningAgents ? `正在协调 ${runningAgents} 个 Agent` : '在线，当前无 Agent 运行'}</p>
          </aside>
        </div>

        <section className="ff-dashboard-panel ff-dashboard-projects">
          <header><div><small>PORTFOLIO</small><h2>我的项目 <span>{projects.length}</span></h2></div><button type="button" onClick={onViewAllProjects}>查看全部<ArrowRight aria-hidden="true" /></button></header>
          <div className="ff-dashboard-project-grid">
            {projects.length ? projects.slice(0, 4).map((project) => {
              const progress = projectProgress(project);
              const status = projectStatus(project);
              const projectTelemetry = telemetry[project.state.session_id];
              const projectTokens = usage(projectTelemetry?.events ?? []);
              const projectTasks = project.resources.workItems.filter((item) => item.kind === 'TASK');
              return (
                <article key={project.state.session_id}>
                  <div className="ff-dashboard-project-head"><span><BriefcaseBusiness aria-hidden="true" /></span><i className={`is-${status.tone}`}>{status.label}</i></div>
                  <h3>{projectTitle(project)}</h3>
                  <p>当前阶段：<strong>{projectStage(project)}</strong></p>
                  <div className="ff-dashboard-progress"><div><i style={{ width: `${progress}%` }} /></div><span>{progress}%</span></div>
                  <dl><div><dt>Token</dt><dd>{formatCount(projectTokens.input + projectTokens.output)}</dd></div><div><dt>Agent</dt><dd>{projectTelemetry?.runtimes.length ?? 0}</dd></div><div><dt>任务</dt><dd>{projectTasks.filter((item) => workItemLane(item.status) === 'done').length}/{projectTasks.length}</dd></div></dl>
                  <button type="button" onClick={() => onOpenProject(project.state.session_id)}>打开项目<ArrowRight aria-hidden="true" /></button>
                </article>
              );
            }) : <div className="ff-dashboard-empty is-wide"><Sparkles aria-hidden="true" /><strong>还没有项目</strong><p>从左侧 Agent Chat 创建项目后，这里会展示进度与运行状态。</p></div>}
          </div>
        </section>

        <section className="ff-dashboard-panel ff-dashboard-notices">
          <header><div><small>ACTIVITY FEED</small><h2>通知</h2></div><span>{notices.length} 条最新动态</span></header>
          <ol>
            {notices.length ? notices.map(({ event, project, sessionId }) => {
              const tone = eventTone(event);
              const root = projects.find((item) => item.state.session_id === sessionId)?.resources.workItems.find((item) => item.kind === 'ROOT');
              return <li key={`${sessionId}:${event.id}`}>
                <span className={`is-${tone}`}>{tone === 'error' ? <AlertTriangle aria-hidden="true" /> : tone === 'success' ? <CheckCircle2 aria-hidden="true" /> : <Bot aria-hidden="true" />}</span>
                <div><strong>{auditTitle(event)}</strong><p>{project} · {eventDetail(event)}</p></div>
                <time>{displayTime(event.created_at)}</time>
                {root && <button type="button" onClick={() => onOpenWorkItem(sessionId, root.id)}>查看</button>}
              </li>;
            }) : <div className="ff-dashboard-empty"><Bot aria-hidden="true" /><strong>暂无通知</strong><p>Agent 报错、审核结果和阶段变化会显示在这里。</p></div>}
          </ol>
        </section>
      </div>
    </div>
  );
}
