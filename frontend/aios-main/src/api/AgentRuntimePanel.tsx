import { useEffect, useState } from 'react';
import type { AgentRuntimeDto } from './dto';
import { displayTime } from './presentation';
import { listAgentRuntime } from './sessions';

const POLL_INTERVAL_MS = 2_000;

const operationLabels: Record<string, string> = {
  analyze_brief: '分析项目需求完整性',
  generate_spec: '生成项目规格',
  review_spec: '审核项目规格质量',
  decompose_spec: '拆解已批准的项目规格',
  plan_task: '生成任务实施步骤与接口',
  review_breakdown: '审核任务拆解结果',
  rewrite_prd: '根据审核批注修订 PRD',
  restore_spec: '恢复历史项目规格',
};

const statusLabels: Record<AgentRuntimeDto['status'], string> = {
  running: '运行中',
  completed: '已完成',
  error: '异常',
};

type RuntimeProject = { sessionId: string; title: string };

function elapsedTime(startedAt: string): string {
  const milliseconds = Math.max(0, Date.now() - Date.parse(startedAt));
  const seconds = Math.floor(milliseconds / 1_000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

function agentTime(agent: AgentRuntimeDto): string {
  if (agent.status === 'running') return `已运行 ${elapsedTime(agent.started_at)}`;
  return displayTime(agent.completed_at ?? agent.started_at);
}

export function AgentRuntimePanel({ projects }: { projects: RuntimeProject[] }) {
  const [snapshots, setSnapshots] = useState<Record<string, AgentRuntimeDto[]>>({});
  const [failedIds, setFailedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    const controllers = new Set<AbortController>();

    const refresh = async () => {
      const settled = await Promise.allSettled(projects.map(async (project) => {
        const controller = new AbortController();
        controllers.add(controller);
        try {
          return { project, agents:await listAgentRuntime(project.sessionId, controller.signal) };
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

  const visibleProjects = projects.map((project) => ({
    project,
    agents:snapshots[project.sessionId],
  }));
  const agents = visibleProjects.flatMap(({ agents }) => agents ?? []);
  const startedCount = agents.length;
  const runningCount = agents.filter((agent) => agent.status === 'running').length;
  const completedCount = agents.filter((agent) => agent.status === 'completed').length;
  const errorCount = agents.filter((agent) => agent.status === 'error').length;
  const failedProjects = projects.filter((project) => failedIds.includes(project.sessionId));

  return (
    <section className="ff-agent-runtime" aria-label="Agent 实时运行状态">
      <header className="ff-agent-runtime-header">
        <h2>Agent 实时运行状态</h2>
        <p>{loading ? '正在加载运行状态' : '每 2 秒刷新一次'}</p>
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
          {failedProjects.map((project) => `${project.title}：暂时无法刷新，显示最近一次成功快照`).join('；')}
        </div>
      )}
      <div className="ff-agent-runtime-projects">
        {!loading && startedCount === 0 && <p>暂无已启动 Agent</p>}
        {visibleProjects.map(({ project, agents }) => (
          agents && agents.length > 0 && (
            <section className="ff-agent-runtime-project" key={project.sessionId} aria-label={`${project.title} Agent 运行状态`}>
              <h3>{project.title}</h3>
              <ul>
                {agents.map((agent) => (
                  <li key={agent.agent_session_id}>
                    <div>
                      <strong>{agent.role}</strong>
                      <span>{statusLabels[agent.status]}</span>
                    </div>
                    <p>{operationLabels[agent.current_operation] ?? agent.current_operation}</p>
                    {agent.current_summary && <p>{agent.current_summary}</p>}
                    <p>{agentTime(agent)}</p>
                    <p>{agent.call_count} 次调用</p>
                  </li>
                ))}
              </ul>
            </section>
          )
        ))}
      </div>
    </section>
  );
}
