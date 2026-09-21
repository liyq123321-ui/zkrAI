import { useEffect, useState } from 'react';
import type {
  AgentRuntimeDto,
  AgentRuntimeEventDto,
  SemanticaDecisionDto,
  SemanticaStatusDto,
} from './dto';
import { displayTime } from './presentation';
import { listAgentRuntime, listAgentRuntimeEvents, getSemanticaStatus, listSemanticaDecisions } from './sessions';
import { AgentCallGraph } from './AgentCallGraph';
import type { RuntimeGraphProject } from './agentRuntimeGraph';
import { WorkItemDialog } from './WorkItemDialog';

const POLL_INTERVAL_MS = 2_000;

export const operationLabels: Record<string, string> = {
  recommend_lifecycle: '评估 SDLC 顶层路线',
  analyze_brief: '分析项目需求完整性',
  generate_spec: '生成项目规格',
  generate_prd_prototype: '生成 PRD 交互原型',
  review_spec: '审核项目规格质量',
  decompose_spec: '拆解已批准的项目规格',
  plan_task: '生成任务实施步骤与接口',
  review_breakdown: '审核任务拆解结果',
  rewrite_prd: '根据审核批注修订 PRD',
  restore_spec: '恢复历史项目规格',
};

export function agentOperationLabel(operation: string): string {
  return operationLabels[operation] ?? operation;
}

const eventTypeLabels: Record<string, string> = {
  reasoning: '推理摘要',
  agent_message: 'Agent 消息',
  command_execution: '命令执行',
  file_change: '文件修改',
  mcp_tool_call: '工具调用',
  web_search: '网页搜索',
  plan_update: '计划更新',
  stderr: '运行诊断',
  stdout: '标准输出',
  structured_output: '结构化输出',
  'thread.started': '会话阶段',
  'turn.started': '执行阶段',
  'turn.completed': '执行阶段',
  'turn.failed': '执行阶段',
};

type SelectedAgent = { sessionId: string; agentSessionId: string };

function selectionKey(sessionId: string, agentSessionId: string): string {
  return `${sessionId}:${agentSessionId}`;
}

function eventLabel(event: AgentRuntimeEventDto): string {
  return eventTypeLabels[event.item_type ?? event.event_type]
    ?? event.item_type
    ?? event.event_type;
}

function AgentTimeline({
  agent,
  events,
  loading,
  failed,
}: {
  agent: AgentRuntimeDto;
  events: AgentRuntimeEventDto[];
  loading: boolean;
  failed: boolean;
}) {
  const currentCallEvents = events.filter((event) => event.agent_call_id === agent.current_call_id);
  const currentReasoning = [...currentCallEvents]
    .reverse()
    .find((event) => event.item_type === 'reasoning');
  const latest = currentCallEvents.at(-1) ?? events.at(-1);

  return (
    <div className="ff-agent-runtime-timeline" aria-live="polite">
      <div className="ff-agent-runtime-disclosure">
        <strong>{agent.status === 'running' ? '当前执行内容' : '最近一次执行内容'}</strong>
        <span>
          {currentReasoning?.detail
            ?? latest?.detail
            ?? latest?.title
            ?? (loading ? '正在读取 Codex 运行记录…' : '暂无运行详情')}
        </span>
      </div>
      <div className="ff-agent-runtime-timeline-header">
        <strong>Codex 运行记录</strong>
        <span>仅记录 CLI 公开的 JSONL 事件和脱敏诊断，不包含隐藏思维链。</span>
      </div>
      {failed && (
        <p role="status" className="ff-agent-runtime-event-warning">
          暂时无法刷新运行记录，下面保留最近一次成功读取的内容。
        </p>
      )}
      {!loading && events.length === 0 && (
        <p className="ff-agent-runtime-event-empty">
          暂无可公开的 Codex 运行记录。功能启用前完成的调用不会补写历史事件。
        </p>
      )}
      {events.length > 0 && (
        <ol className="ff-agent-runtime-events">
          {events.map((event) => (
            <li key={event.id}>
              <div className="ff-agent-runtime-event-meta">
                <time dateTime={event.created_at}>{displayTime(event.created_at)}</time>
                <span>{eventLabel(event)}</span>
                <span>{agentOperationLabel(event.operation)}</span>
                <code>{event.agent_call_id.slice(0, 8)}</code>
              </div>
              <strong>{event.title}</strong>
              {event.detail && <pre>{event.detail}</pre>}
              <details>
                <summary>查看脱敏事件数据</summary>
                <pre>{JSON.stringify(event.payload, null, 2)}</pre>
              </details>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

type DecisionState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'disabled' }
  | { kind: 'unreachable' }
  | { kind: 'loaded'; decisions: SemanticaDecisionDto[] };

function SemanticaDecisions({ state }: { state: DecisionState }) {
  if (state.kind === 'idle' || state.kind === 'loading') {
    return <p className="ff-semantica-hint">正在读取 Semantica 决策溯源…</p>;
  }
  if (state.kind === 'disabled') {
    return <p className="ff-semantica-hint">Semantica 行为审计未启用，暂无可追溯的决策记录。</p>;
  }
  if (state.kind === 'unreachable') {
    return <p className="ff-semantica-hint">无法连接 Neo4j 图谱数据库，决策溯源暂不可用。</p>;
  }
  if (state.kind === 'loaded' && state.decisions.length === 0) {
    return <p className="ff-semantica-hint">该 Agent 会话暂无 Semantica 决策记录。</p>;
  }
  return (
    <ol className="ff-semantica-decisions">
      {state.decisions.map((decision, index) => (
        <li key={decision.decision_id ?? index}>
          <div className="ff-semantica-decision-meta">
            <span>{decision.scenario ? agentOperationLabel(decision.scenario) : 'LLM 决策'}</span>
            {typeof decision.confidence === 'number' && (
              <span>置信度 {decision.confidence.toFixed(2)}</span>
            )}
            {decision.decision_maker && <code>{decision.decision_maker}</code>}
          </div>
          {decision.reasoning && <p className="ff-semantica-reasoning">{decision.reasoning}</p>}
          {decision.outcome && (
            <p className="ff-semantica-outcome">结果契约：{decision.outcome}</p>
          )}
        </li>
      ))}
    </ol>
  );
}

export function AgentRuntimePanel({ projects }: { projects: RuntimeGraphProject[] }) {
  const [snapshots, setSnapshots] = useState<Record<string, AgentRuntimeDto[]>>({});
  const [failedIds, setFailedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<SelectedAgent | null>(null);
  const [eventSnapshots, setEventSnapshots] = useState<Record<string, AgentRuntimeEventDto[]>>({});
  const [eventLoading, setEventLoading] = useState(false);
  const [eventFailed, setEventFailed] = useState(false);

  const [semanticaStatus, setSemanticaStatus] = useState<SemanticaStatusDto | null>(null);
  const [decisionState, setDecisionState] = useState<DecisionState>({ kind: 'idle' });

  // Derived from snapshots, but stable across the 2s polling refresh: the
  // project id for a given agent never changes, so keying the decisions effect
  // on this value (not the whole snapshots map) avoids a refetch flicker.
  const selectedProjectId = selected === null
    ? undefined
    : snapshots[selected.sessionId]?.find(
        (agent) => agent.agent_session_id === selected.agentSessionId,
      )?.project_id;

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    getSemanticaStatus(controller.signal)
      .then((status) => { if (!disposed) setSemanticaStatus(status); })
      .catch(() => { if (!disposed) setSemanticaStatus(null); });
    return () => { disposed = true; controller.abort(); };
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    const controllers = new Set<AbortController>();

    const refresh = async () => {
      const settled = await Promise.allSettled(projects.map(async (project) => {
        const controller = new AbortController();
        controllers.add(controller);
        try {
          return { project, agents: await listAgentRuntime(project.sessionId, controller.signal) };
        } finally {
          controllers.delete(controller);
        }
      }));
      if (disposed) return;

      const failures: string[] = [];
      setSnapshots((previous) => {
        const next = { ...previous };
        settled.forEach((result, index) => {
          if (result.status === 'fulfilled') next[result.value.project.sessionId] = result.value.agents;
          else failures.push(projects[index].sessionId);
        });
        return next;
      });
      setFailedIds(failures);
      setLoading(false);
      timer = window.setTimeout(refresh, POLL_INTERVAL_MS);
    };

    void refresh();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      controllers.forEach((controller) => controller.abort());
    };
  }, [projects]);

  useEffect(() => {
    if (selected === null) {
      setEventLoading(false);
      setEventFailed(false);
      return undefined;
    }
    let disposed = false;
    let timer: number | undefined;
    let controller: AbortController | undefined;
    const key = selectionKey(selected.sessionId, selected.agentSessionId);

    const refresh = async () => {
      controller = new AbortController();
      try {
        const events = await listAgentRuntimeEvents(
          selected.sessionId,
          selected.agentSessionId,
          controller.signal,
        );
        if (disposed) return;
        setEventSnapshots((previous) => ({ ...previous, [key]: events }));
        setEventFailed(false);
      } catch {
        if (!disposed && !controller.signal.aborted) setEventFailed(true);
      } finally {
        if (!disposed) {
          setEventLoading(false);
          timer = window.setTimeout(refresh, POLL_INTERVAL_MS);
        }
      }
    };

    setEventLoading(true);
    setEventFailed(false);
    void refresh();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      controller?.abort();
    };
  }, [selected]);

  // Semantica decision provenance for the currently selected agent session.
  useEffect(() => {
    if (selected === null) {
      setDecisionState({ kind: 'idle' });
      return undefined;
    }
    if (!semanticaStatus?.configured) {
      setDecisionState({ kind: 'disabled' });
      return undefined;
    }
    if (!selectedProjectId) {
      // Agent snapshot may not have loaded yet; stay in loading until it does.
      setDecisionState({ kind: 'loading' });
      return undefined;
    }
    let disposed = false;
    let controller: AbortController | undefined;
    const load = async () => {
      controller = new AbortController();
      setDecisionState({ kind: 'loading' });
      try {
        const result = await listSemanticaDecisions(
          selectedProjectId,
          selected.agentSessionId,
          controller.signal,
        );
        if (disposed) return;
        if (!result.available) {
          setDecisionState({ kind: 'unreachable' });
          return;
        }
        setDecisionState({ kind: 'loaded', decisions: result.decisions });
      } catch {
        if (!disposed && !controller.signal.aborted) setDecisionState({ kind: 'unreachable' });
      }
    };
    void load();
    return () => {
      disposed = true;
      controller?.abort();
    };
  }, [selected?.sessionId, selected?.agentSessionId, selectedProjectId, semanticaStatus]);

  const visibleProjects = projects.map((project) => ({
    project,
    agents: snapshots[project.sessionId],
  }));
  const agents = visibleProjects.flatMap(({ agents }) => agents ?? []);
  const startedCount = agents.length;
  const runningCount = agents.filter((agent) => agent.status === 'running').length;
  const completedCount = agents.filter((agent) => agent.status === 'completed').length;
  const errorCount = agents.filter((agent) => agent.status === 'error').length;
  const failedProjects = projects.filter((project) => failedIds.includes(project.sessionId));
  const hasSnapshot = (sessionId: string) =>
    Object.prototype.hasOwnProperty.call(snapshots, sessionId);
  const hasUnknownProject = projects.some((project) => !hasSnapshot(project.sessionId));

  const selectedProject = selected
    ? projects.find((candidate) => candidate.sessionId === selected.sessionId)
    : undefined;
  const selectedAgent = selected
    ? snapshots[selected.sessionId]?.find(
        (candidate) => candidate.agent_session_id === selected.agentSessionId,
      )
    : undefined;

  const statusBadge = (() => {
    if (!semanticaStatus) return null;
    if (!semanticaStatus.configured) {
      return <span className="ff-audit-badge ff-audit-badge-off">行为溯源：未启用</span>;
    }
    if (!semanticaStatus.available) {
      return <span className="ff-audit-badge ff-audit-badge-warn">行为溯源：已启用 · Neo4j 未连接</span>;
    }
    return <span className="ff-audit-badge ff-audit-badge-on">行为溯源：已启用 · Neo4j 已连接</span>;
  })();

  return (
    <section className="ff-agent-runtime" aria-label="Agent 实时运行状态">
      <header className="ff-agent-runtime-header">
        <h2>Agent 实时运行状态</h2>
        <p>{loading ? '正在加载运行状态' : '每 2 秒刷新一次 · 点击 Agent 查看执行记录'}</p>
        {statusBadge}
      </header>
      <dl className="ff-agent-runtime-metrics">
        <div>
          <dt>已启动</dt>
          <dd>{startedCount}</dd>
        </div>
        <div>
          <dt>运行中</dt>
          <dd>{runningCount}</dd>
        </div>
        <div>
          <dt>已完成</dt>
          <dd>{completedCount}</dd>
        </div>
        <div>
          <dt>异常</dt>
          <dd>{errorCount}</dd>
        </div>
      </dl>
      {failedProjects.length > 0 && (
        <div role="status" className="ff-agent-runtime-warning">
          {failedProjects.map((project) => hasSnapshot(project.sessionId)
            ? `${project.title}：暂时无法刷新，显示最近一次成功快照`
            : `${project.title}：尚未取得运行快照`).join('；')}
        </div>
      )}
      <div className="ff-agent-runtime-projects">
        {!loading && !hasUnknownProject && startedCount === 0 && <p>暂无已启动 Agent</p>}
        {visibleProjects.map(({ project, agents }) => {
          const selectedAgentForProject = agents?.find((agent) => (
            selected?.sessionId === project.sessionId
            && selected.agentSessionId === agent.agent_session_id
          ));
          return (
            <div className="ff-agent-runtime-project-wrap" key={project.sessionId}>
              <AgentCallGraph
                project={project}
                agents={agents ?? []}
                selectedAgentSessionId={selectedAgentForProject?.agent_session_id ?? null}
                onSelectAgent={(agent) => setSelected({
                  sessionId: project.sessionId,
                  agentSessionId: agent.agent_session_id,
                })}
              />
            </div>
          );
        })}
      </div>
      {selected && selectedProject && selectedAgent && (
        <WorkItemDialog
          title={`Agent 运行记录 · ${selectedAgent.role}`}
          contentKey={selectionKey(selected.sessionId, selected.agentSessionId)}
          onClose={() => setSelected(null)}
        >
          <div className="ff-agent-runtime-modal">
            <div className="ff-agent-runtime-modal-context">
              <strong>{selectedProject.title}</strong>
              <span>{operationLabels[selectedAgent.current_operation] ?? selectedAgent.current_operation}</span>
            </div>
            <AgentTimeline
              agent={selectedAgent}
              events={eventSnapshots[selectionKey(selected.sessionId, selected.agentSessionId)] ?? []}
              loading={eventLoading}
              failed={eventFailed}
            />
            <div className="ff-semantica-section">
              <h3>Semantica 决策溯源</h3>
              <p className="ff-semantica-sub">
                来自 Neo4j PROV 属性图的 LLM 决策记录（推理、结果契约、置信度）。
              </p>
              <SemanticaDecisions state={decisionState} />
            </div>
          </div>
        </WorkItemDialog>
      )}
    </section>
  );
}
