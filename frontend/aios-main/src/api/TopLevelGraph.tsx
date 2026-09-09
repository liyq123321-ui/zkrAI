import { useState, type ReactNode } from 'react';
import { Activity, AlertCircle, Bot, CheckCircle2, Clock3, Cpu, GitBranch, Layers3, UserRound, Zap } from 'lucide-react';
import type { AgentRuntimeDto, AgentRuntimeEventDto, AgentSpecDto, LifecycleRouteDecisionDto, WorkItemDto } from './dto';
import { agentOperationLabel } from './AgentRuntimePanel';
import { MilestoneTaskMonitor } from './MilestoneTaskMonitor';
import { WorkItemDialog } from './WorkItemDialog';
import { workItemLane } from './workflowUi';

type LifecyclePhase = {
  milestone_key: string;
  stage_id: string;
  iteration: number;
  exit_gate_key: string;
  schedule: string;
};

type LifecycleTask = {
  task_key: string;
  activity_ids: string[];
  deliverable_ids: string[];
};

type LifecycleStage = {
  id: string;
  number: number;
  name: string;
  scope: 'project' | 'global' | 'iteration' | 'continuous';
  objective: string;
  entry_criteria: string;
  exit_criteria: string;
  owner: string;
  schedule_guidance: string;
  deliverables: Array<{ id: string; name: string }>;
};

type PersistedLifecycle = {
  model: string;
  rules_version: string;
  rules_hash: string;
  selection_reason: string;
  phases: LifecyclePhase[];
  task: LifecycleTask;
  model_rules: {
    id: string;
    name: string;
    stages: LifecycleStage[];
  };
};

export type TopLevelPhase = LifecyclePhase & {
  stage: LifecycleStage;
  milestone?: WorkItemDto;
  tasks: WorkItemDto[];
  status: 'waiting' | 'active' | 'blocked' | 'complete';
  completedTasks: number;
};

export type TopLevelPlan = {
  model: string;
  modelName: string;
  rulesVersion: string;
  rulesHash: string;
  selectionReason: string;
  phases: TopLevelPhase[];
  inconsistent: boolean;
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string') ? value : [];
}

function parseLifecycle(value: unknown): PersistedLifecycle | null {
  const data = record(value);
  const modelRules = record(data?.model_rules);
  if (!data || !modelRules || typeof data.model !== 'string' || typeof modelRules.name !== 'string') return null;
  if (!Array.isArray(data.phases) || !Array.isArray(modelRules.stages)) return null;
  const phases = data.phases.map(record).filter((item): item is Record<string, unknown> => Boolean(item)).map((item) => ({
    milestone_key: String(item.milestone_key ?? ''),
    stage_id: String(item.stage_id ?? ''),
    iteration: Number(item.iteration ?? 0),
    exit_gate_key: String(item.exit_gate_key ?? ''),
    schedule: String(item.schedule ?? ''),
  })).filter((item) => item.milestone_key && item.stage_id);
  const stages = modelRules.stages.map(record).filter((item): item is Record<string, unknown> => Boolean(item)).map((item) => ({
    id: String(item.id ?? ''),
    number: Number(item.number ?? 0),
    name: String(item.name ?? ''),
    scope: String(item.scope ?? 'project') as LifecycleStage['scope'],
    objective: String(item.objective ?? ''),
    entry_criteria: String(item.entry_criteria ?? ''),
    exit_criteria: String(item.exit_criteria ?? ''),
    owner: String(item.owner ?? ''),
    schedule_guidance: String(item.schedule_guidance ?? ''),
    deliverables: Array.isArray(item.deliverables)
      ? item.deliverables.map(record).filter((entry): entry is Record<string, unknown> => Boolean(entry)).map((entry) => ({
        id: String(entry.id ?? ''), name: String(entry.name ?? ''),
      })).filter((entry) => entry.id && entry.name)
      : [],
  })).filter((item) => item.id && item.name);
  if (!phases.length || !stages.length) return null;
  const task = record(data.task);
  return {
    model: data.model,
    rules_version: String(data.rules_version ?? ''),
    rules_hash: String(data.rules_hash ?? ''),
    selection_reason: String(data.selection_reason ?? ''),
    phases,
    task: {
      task_key: String(task?.task_key ?? ''),
      activity_ids: strings(task?.activity_ids),
      deliverable_ids: strings(task?.deliverable_ids),
    },
    model_rules: { id: String(modelRules.id ?? data.model), name: modelRules.name, stages },
  };
}

function phaseStatus(tasks: WorkItemDto[], exitGateKey: string): TopLevelPhase['status'] {
  const gate = tasks.find((item) => item.local_key === exitGateKey);
  const lanes = tasks.map((item) => workItemLane(item.status));
  const rawStatuses = tasks.map((item) => String(item.status ?? '').toLowerCase());
  if (gate && workItemLane(gate.status) === 'done') return 'complete';
  if (rawStatuses.some((status) => status === 'failed' || status === 'blocked')) return 'blocked';
  if (lanes.some((lane) => lane === 'in_progress') || lanes.some((lane) => lane === 'done')) return 'active';
  return 'waiting';
}

function activateCurrentPhase(phases: TopLevelPhase[]): TopLevelPhase[] {
  if (phases.some((phase) => phase.status === 'active' || phase.status === 'blocked')) return phases;
  const currentIndex = phases.findIndex((phase) => phase.status === 'waiting');
  if (currentIndex < 0) return phases;
  return phases.map((phase, index) => index === currentIndex ? { ...phase, status: 'active' } : phase);
}

function previewScope(model: string, stageId: string): LifecycleStage['scope'] {
  if (model !== 'iterative_incremental') return 'project';
  if (stageId === 'backlog') return 'global';
  if (stageId === 'operations') return 'continuous';
  return 'iteration';
}

function buildSelectedRoutePlan(decision: LifecycleRouteDecisionDto): TopLevelPlan | null {
  if (!decision.selected_model) return null;
  const option = decision.options.find((candidate) => candidate.model === decision.selected_model);
  if (!option) return null;
  const phases = option.stages.map((stage, index): TopLevelPhase => ({
    milestone_key: `selected-route:${stage.id}`,
    stage_id: stage.id,
    iteration: 0,
    exit_gate_key: '任务拆分后生成',
    schedule: stage.schedule,
    stage: {
      id: stage.id,
      number: stage.number || index + 1,
      name: stage.name,
      scope: option.model === 'iterative_incremental'
        ? previewScope(option.model, stage.id)
        : stage.scope ?? 'project',
      objective: stage.objective,
      entry_criteria: stage.entry_criteria || (index === 0 ? '路线已确认，进入首阶段' : '上一阶段满足所选路线的准出门禁'),
      exit_criteria: stage.exit_criteria || '完整任务拆分后按 YAML 阶段门禁执行',
      owner: stage.owner || '任务拆分后分配',
      schedule_guidance: stage.schedule,
      deliverables: stage.deliverables ?? [],
    },
    tasks: [],
    status: index === 0 ? 'active' : 'waiting',
    completedTasks: 0,
  }));
  return {
    model: option.model,
    modelName: option.name,
    rulesVersion: decision.rules_version,
    rulesHash: decision.rules_hash,
    selectionReason: option.reason || decision.assessment.summary,
    phases,
    inconsistent: false,
  };
}

export function buildTopLevelPlan(
  agentSpecs: AgentSpecDto[],
  workItems: WorkItemDto[],
  decision?: LifecycleRouteDecisionDto | null,
): TopLevelPlan | null {
  const lifecycles = agentSpecs.map((item) => parseLifecycle(item.content.sdlc)).filter((item): item is PersistedLifecycle => Boolean(item));
  if (!lifecycles.length) return decision ? buildSelectedRoutePlan(decision) : null;
  const source = lifecycles[0];
  const signature = `${source.model}:${source.rules_version}:${source.rules_hash}`;
  const inconsistent = lifecycles.some((item) => `${item.model}:${item.rules_version}:${item.rules_hash}` !== signature);
  const stageById = new Map(source.model_rules.stages.map((stage) => [stage.id, stage]));
  const stageOrder = new Map(source.model_rules.stages.map((stage, index) => [stage.id, index]));
  const milestoneByKey = new Map(workItems.filter((item) => item.kind === 'MILESTONE' && item.local_key)
    .map((item) => [item.local_key!, item]));
  const orderedPhases = [...source.phases].sort((left, right) => {
    const leftStage = stageById.get(left.stage_id);
    const rightStage = stageById.get(right.stage_id);
    const group = (stage?: LifecycleStage) =>
      stage?.scope === 'global' ? 0 : stage?.scope === 'continuous' ? 2 : 1;
    return group(leftStage) - group(rightStage)
      || left.iteration - right.iteration
      || (stageOrder.get(left.stage_id) ?? 999) - (stageOrder.get(right.stage_id) ?? 999);
  });
  const phases = orderedPhases.flatMap((phase): TopLevelPhase[] => {
    const stage = stageById.get(phase.stage_id);
    if (!stage) return [];
    const milestone = milestoneByKey.get(phase.milestone_key);
    const tasks = milestone ? workItems.filter((item) => item.kind === 'TASK' && item.parent_id === milestone.id) : [];
    return [{
      ...phase,
      stage,
      milestone,
      tasks,
      status: phaseStatus(tasks, phase.exit_gate_key),
      completedTasks: tasks.filter((item) => workItemLane(item.status) === 'done').length,
    }];
  });
  return {
    model: source.model,
    modelName: source.model_rules.name,
    rulesVersion: source.rules_version,
    rulesHash: source.rules_hash,
    selectionReason: source.selection_reason,
    phases: activateCurrentPhase(phases),
    inconsistent,
  };
}

function assignee(item: WorkItemDto): string {
  return item.suggested_assignee || item.responsible_role || '待分配 Agent';
}

function tokenUsage(events: AgentRuntimeEventDto[]): { input: number; output: number; total: number } {
  let input = 0;
  let output = 0;
  for (const event of events) {
    if (event.event_type !== 'turn.completed') continue;
    const usage = record(event.payload.usage);
    const inputValue = Number(usage?.input_tokens ?? 0);
    const outputValue = Number(usage?.output_tokens ?? 0);
    if (Number.isFinite(inputValue) && inputValue > 0) input += inputValue;
    if (Number.isFinite(outputValue) && outputValue > 0) output += outputValue;
  }
  return { input, output, total: input + output };
}

function looksLikeAgent(value: string): boolean {
  return /agent|codex|机器人|system|reviewer|project manager|\bpm\b/i.test(value);
}

function formatCount(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value);
}

function MetricDetail({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="ff-top-level-metric-detail" role="tooltip">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

const statusLabels: Record<TopLevelPhase['status'], string> = {
  waiting: '等待进入',
  active: '进行中',
  blocked: '受阻',
  complete: '已完成',
};

function phaseLabel(phase: TopLevelPhase): string {
  if (phase.stage.scope === 'global') return '全局启动';
  if (phase.stage.scope === 'continuous') return '贯穿全程';
  if (phase.iteration > 0) return `迭代 ${phase.iteration} · 阶段 ${phase.stage.number}`;
  return `阶段 ${phase.stage.number}`;
}

export function TopLevelGraph({
  projectTitle,
  agentSpecs,
  workItems,
  decision = null,
  runtimes = [],
  runtimeEvents = [],
  projectPhase = '等待项目状态',
  onOpenWorkItem,
}: {
  projectTitle: string;
  agentSpecs: AgentSpecDto[];
  workItems: WorkItemDto[];
  decision?: LifecycleRouteDecisionDto | null;
  runtimes?: AgentRuntimeDto[];
  runtimeEvents?: AgentRuntimeEventDto[];
  projectPhase?: string;
  onOpenWorkItem: (workItemId: string) => void;
}) {
  const [selectedPhaseKey, setSelectedPhaseKey] = useState<string | null>(null);
  const plan = buildTopLevelPlan(agentSpecs, workItems, decision);
  if (!plan) {
    return (
      <div className="ff-top-level-empty" role="status">
        <Layers3 aria-hidden="true" />
        <strong>尚未生成顶层阶段图</strong>
        <p>完成需求澄清并选择 SDLC 路线后，这里会立即显示生命周期流程。</p>
      </div>
    );
  }
  const currentPhase = plan.phases.find((phase) => phase.status === 'active' || phase.status === 'blocked')
    ?? plan.phases.at(-1);
  const assignedItems = workItems.filter((item) => item.kind !== 'MILESTONE' && assignee(item) !== '待分配 Agent');
  const assignedNames = new Set(assignedItems.map(assignee));
  const employees = [...assignedNames].filter((name) => !looksLikeAgent(name));
  const assignedAgentNames = [...assignedNames].filter(looksLikeAgent);
  const runtimeRoleNames = new Set(runtimes.map((runtime) => runtime.role.toLowerCase()));
  const agentCount = runtimes.length + assignedAgentNames.filter((name) => !runtimeRoleNames.has(name.toLowerCase())).length;
  const usage = tokenUsage(runtimeEvents);
  const completedPhases = plan.phases.filter((phase) => phase.status === 'complete').length;
  const taskCount = workItems.filter((item) => item.kind === 'TASK').length;
  const completedTasks = workItems.filter((item) => item.kind === 'TASK' && workItemLane(item.status) === 'done').length;
  const callCount = runtimes.reduce((total, runtime) => total + runtime.call_count, 0);
  const selectedPhase = plan.phases.find((phase) => `${phase.iteration}:${phase.stage_id}` === selectedPhaseKey) ?? null;
  const selectedMilestone: WorkItemDto | null = selectedPhase ? selectedPhase.milestone ?? {
    id: selectedPhase.milestone_key || selectedPhase.stage_id,
    local_key: selectedPhase.milestone_key || selectedPhase.stage_id,
    parent_id: null,
    kind: 'MILESTONE',
    title: selectedPhase.stage.name,
    description: selectedPhase.stage.objective,
    summary: null,
    objective: selectedPhase.stage.objective,
    status: selectedPhase.status === 'complete' ? 'completed' : selectedPhase.status === 'active' ? 'in_progress' : selectedPhase.status,
    scope: null,
    exclusions: null,
    outputs: selectedPhase.stage.deliverables,
    acceptance_criteria: null,
    required_skills: null,
    responsible_role: selectedPhase.stage.owner,
    suggested_assignee: null,
    dependency_work_item_ids: [],
  } : null;
  return (
    <>
    <div className="ff-top-level-console">
      <aside className="ff-top-level-monitor" aria-label="项目监控统计">
        <header><span><Activity aria-hidden="true" /></span><div><small>PROJECT TELEMETRY</small><strong>全局监控</strong></div><i /></header>
        <section className="ff-top-level-token-stat ff-top-level-metric" tabIndex={0}>
          <span><Zap aria-hidden="true" />累计 Token</span>
          <strong>{formatCount(usage.total)}</strong>
          <small>输入 {formatCount(usage.input)} · 输出 {formatCount(usage.output)}</small>
          <MetricDetail title="Token 消耗明细">
            <table><tbody>
              <tr><th>输入 Token</th><td>{formatCount(usage.input)}</td></tr>
              <tr><th>输出 Token</th><td>{formatCount(usage.output)}</td></tr>
              <tr><th>合计</th><td>{formatCount(usage.total)}</td></tr>
            </tbody></table>
          </MetricDetail>
        </section>
        <div className="ff-top-level-stat-grid">
          <div className="ff-top-level-metric" tabIndex={0}>
            <Bot aria-hidden="true" /><span>已分配 Agent</span><strong>{agentCount}</strong>
            <MetricDetail title="Agent 明细">
              {agentCount ? <table><thead><tr><th>Agent</th><th>状态</th><th>调用</th></tr></thead><tbody>
                {runtimes.map((runtime) => <tr key={runtime.agent_session_id}><th>{runtime.role}</th><td>{runtime.status === 'running' ? '运行中' : runtime.status === 'completed' ? '已完成' : '异常'}</td><td>{runtime.call_count}</td></tr>)}
                {assignedAgentNames.filter((name) => !runtimeRoleNames.has(name.toLowerCase())).map((name) => <tr key={name}><th>{name}</th><td>已分配</td><td>—</td></tr>)}
              </tbody></table> : <p>尚未分配 Agent</p>}
            </MetricDetail>
          </div>
          <div className="ff-top-level-metric" tabIndex={0}>
            <UserRound aria-hidden="true" /><span>已分配员工</span><strong>{employees.length}</strong>
            <MetricDetail title="员工分配明细">
              {employees.length ? <table><thead><tr><th>员工</th><th>任务数</th></tr></thead><tbody>
                {employees.map((name) => <tr key={name}><th>{name}</th><td>{assignedItems.filter((item) => assignee(item) === name).length}</td></tr>)}
              </tbody></table> : <p>尚未分配员工</p>}
            </MetricDetail>
          </div>
          <div className="ff-top-level-metric" tabIndex={0}>
            <Cpu aria-hidden="true" /><span>Agent 调用</span><strong>{callCount}</strong>
            <MetricDetail title="Agent 调用明细">
              {runtimes.length ? <table><thead><tr><th>Agent</th><th>当前操作</th><th>次数</th></tr></thead><tbody>
                {runtimes.map((runtime) => <tr key={runtime.agent_session_id}><th>{runtime.role}</th><td>{agentOperationLabel(runtime.current_operation)}</td><td>{runtime.call_count}</td></tr>)}
              </tbody></table> : <p>暂无 Agent 调用</p>}
            </MetricDetail>
          </div>
          <div className="ff-top-level-metric" tabIndex={0}>
            <Layers3 aria-hidden="true" /><span>阶段数量</span><strong>{plan.phases.length}</strong>
            <MetricDetail title="SDLC 阶段明细">
              <table><thead><tr><th>阶段</th><th>状态</th><th>任务</th></tr></thead><tbody>
                {plan.phases.map((phase) => <tr key={`${phase.iteration}:${phase.stage_id}`}><th>{phase.stage.name}</th><td>{statusLabels[phase.status]}</td><td>{phase.completedTasks}/{phase.tasks.length}</td></tr>)}
              </tbody></table>
            </MetricDetail>
          </div>
        </div>
        <section className="ff-top-level-progress-panel">
          <div><span>阶段进度</span><strong>{completedPhases}/{plan.phases.length}</strong></div>
          <div className="ff-top-level-progress-track"><i style={{ width: `${plan.phases.length ? completedPhases / plan.phases.length * 100 : 0}%` }} /></div>
          <small>任务完成 {completedTasks}/{taskCount}</small>
        </section>
        <section className="ff-top-level-current-panel">
          <span>CURRENT STAGE</span>
          <strong>{currentPhase?.stage.name ?? '等待路线'}</strong>
          <p>{currentPhase?.stage.objective ?? '等待项目数据'}</p>
        </section>
        <dl className="ff-top-level-system-table">
          <div><dt>业务状态</dt><dd>{projectPhase}</dd></div>
          <div><dt>运行 Agent</dt><dd>{runtimes.filter((runtime) => runtime.status === 'running').length}</dd></div>
          <div><dt>规则版本</dt><dd>v{plan.rulesVersion}</dd></div>
          <div><dt>规则指纹</dt><dd>{plan.rulesHash.slice(0, 10)}</dd></div>
        </dl>
      </aside>
      <section className="ff-top-level-plan" aria-label={`${projectTitle}顶层阶段图`}>
      {plan.inconsistent && (
        <div className="ff-top-level-warning" role="alert"><AlertCircle aria-hidden="true" />检测到 Agent Spec 的 SDLC 版本不一致，请重新拆分。</div>
      )}
      <header className="ff-top-level-summary">
        <div className="ff-top-level-model-icon"><GitBranch aria-hidden="true" /></div>
        <div>
          <span>已选择 SDLC 顶层路线</span>
          <h2>{plan.modelName}</h2>
          <p>{projectTitle} · {plan.selectionReason || '依据已批准 PRD 的选择清单确定'}</p>
        </div>
        <div className="ff-top-level-version">RULESET v{plan.rulesVersion}<small>{plan.rulesHash.slice(0, 10)}</small></div>
      </header>
      <div className="ff-top-level-flow">
        {plan.phases.map((phase, index) => {
          const phaseKey = `${phase.iteration}:${phase.stage_id}`;
          const body = (
            <>
              <div className="ff-top-level-phase-head">
                <span className="ff-top-level-index">{phaseLabel(phase)}</span>
                <span className={`ff-top-level-status is-${phase.status}`}>
                  {phase.status === 'complete' ? <CheckCircle2 aria-hidden="true" /> : <Clock3 aria-hidden="true" />}
                  {statusLabels[phase.status]}
                </span>
              </div>
              <h3>{phase.stage.name}</h3>
              <p className="ff-top-level-objective">{phase.stage.objective}</p>
              <dl className="ff-top-level-meta">
                <div><dt>准入</dt><dd>{phase.stage.entry_criteria}</dd></div>
                <div><dt>准出</dt><dd>{phase.stage.exit_criteria}</dd></div>
                <div><dt>责任</dt><dd>{phase.stage.owner}</dd></div>
                <div><dt>排期</dt><dd>{phase.schedule || phase.stage.schedule_guidance}</dd></div>
              </dl>
              <div className="ff-top-level-deliverables">
                <strong>关键交付物</strong>
                <div>{phase.stage.deliverables.length
                  ? phase.stage.deliverables.map((item) => <span key={item.id}>{item.name}</span>)
                  : <span>完整规划生成后显示</span>}</div>
              </div>
              <footer>
                <span>{phase.tasks.length ? `${phase.completedTasks}/${phase.tasks.length} 项任务完成` : '等待任务数据'}</span>
                <span>查看阶段详情与子任务关系</span>
              </footer>
            </>
          );
          return (
            <div key={phaseKey} className="ff-top-level-step">
              {index > 0 && <div className="ff-top-level-connector" aria-hidden="true"><span>↓</span></div>}
              <button
                type="button"
                className={`ff-top-level-card is-${phase.status}`}
                aria-haspopup="dialog"
                onClick={() => setSelectedPhaseKey(phaseKey)}
              >
                {body}
              </button>
            </div>
          );
        })}
      </div>
      </section>
    </div>
    {selectedPhase && selectedMilestone && (
      <WorkItemDialog title="SDLC 阶段详情" contentKey={selectedPhaseKey ?? undefined} onClose={() => setSelectedPhaseKey(null)}>
        <MilestoneTaskMonitor
          milestone={selectedMilestone}
          workItems={workItems}
          onOpenWorkItem={(workItemId) => {
            setSelectedPhaseKey(null);
            onOpenWorkItem(workItemId);
          }}
        />
      </WorkItemDialog>
    )}
    </>
  );
}
