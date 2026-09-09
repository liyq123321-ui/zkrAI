import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Bot, GitBranch } from 'lucide-react';
import type { AgentRuntimeDto } from './dto';
import { displayTime } from './presentation';
import {
  buildRuntimeAgentGraph,
  type RuntimeGraphEdge,
  type RuntimeGraphNode,
  type RuntimeGraphNodeStatus,
  type RuntimeGraphProject,
} from './agentRuntimeGraph';

const operationLabels: Record<string, string> = {
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

const statusLabels: Record<RuntimeGraphNodeStatus, string> = {
  planned: '待调用',
  ready: '已就绪',
  running: '运行中',
  completed: '已完成',
  blocked: '已阻塞',
  error: '异常',
};

type MeasuredEdge = RuntimeGraphEdge & {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

function elapsedTime(startedAt: string): string {
  const milliseconds = Math.max(0, Date.now() - Date.parse(startedAt));
  const seconds = Math.floor(milliseconds / 1_000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

function AgentGraphNode({
  node,
  selected,
  setRef,
  onSelect,
}: {
  node: RuntimeGraphNode;
  selected: boolean;
  setRef: (element: HTMLElement | null) => void;
  onSelect: (agent: AgentRuntimeDto) => void;
}) {
  const className = [
    'ff-agent-call-node',
    `is-${node.status}`,
    `is-${node.kind}`,
    selected ? 'is-selected' : '',
  ].filter(Boolean).join(' ');
  const content = (
    <>
      <span className="ff-agent-call-node-icon"><Bot aria-hidden="true" /></span>
      <span className="ff-agent-call-node-copy">
        <span className="ff-agent-call-node-heading">
          <strong>{node.role}</strong>
          <em className={`is-${node.status}`}>{statusLabels[node.status]}</em>
        </span>
        <span className="ff-agent-call-node-title">{node.title}</span>
        {node.runtime ? (
          <span className="ff-agent-call-node-runtime">
            <small>{operationLabels[node.runtime.current_operation] ?? node.runtime.current_operation}</small>
            <small className="ff-agent-call-node-facts">
              <span>开始时间：{displayTime(node.runtime.started_at)}</span>
              <span>{node.runtime.status === 'running'
                ? `持续时间：${elapsedTime(node.runtime.started_at)}`
                : `完成时间：${node.runtime.completed_at ? displayTime(node.runtime.completed_at) : '—'}`}</span>
              <span>{node.runtime.call_count} 次调用</span>
            </small>
          </span>
        ) : node.workItem ? (
          <small>#{node.workItem.id} · {node.summary || '等待执行'}</small>
        ) : (
          <small>{node.status === 'ready' ? '项目创建后保持可用' : '等待上游 Agent 完成后调用'}</small>
        )}
      </span>
      {node.runtime && <span className="ff-agent-call-node-action">查看记录</span>}
    </>
  );

  return (
    <li>
      {node.runtime ? (
        <button
          ref={(element) => setRef(element)}
          type="button"
          className={className}
          aria-haspopup="dialog"
          aria-current={node.status === 'running' ? 'step' : undefined}
          onClick={() => onSelect(node.runtime!)}
        >
          {content}
        </button>
      ) : (
        <article
          ref={(element) => setRef(element)}
          className={className}
          aria-label={`${node.role} ${statusLabels[node.status]}`}
        >
          {content}
        </article>
      )}
    </li>
  );
}

export function AgentCallGraph({
  project,
  agents,
  selectedAgentSessionId,
  onSelectAgent,
}: {
  project: RuntimeGraphProject;
  agents: AgentRuntimeDto[];
  selectedAgentSessionId: string | null;
  onSelectAgent: (agent: AgentRuntimeDto) => void;
}) {
  const graph = useMemo(
    () => buildRuntimeAgentGraph(project, agents),
    [project, agents],
  );
  const graphRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLElement>());
  const [measuredEdges, setMeasuredEdges] = useState<MeasuredEdge[]>([]);
  const markerId = `ff-agent-call-arrow-${useId().replaceAll(':', '')}`;

  const measureEdges = useCallback(() => {
    const container = graphRef.current;
    if (!container) return;
    const containerRect = container.getBoundingClientRect();
    const next: MeasuredEdge[] = [];
    for (const edge of graph.edges) {
      const source = nodeRefs.current.get(edge.sourceId);
      const target = nodeRefs.current.get(edge.targetId);
      if (!source || !target) continue;
      const sourceRect = source.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const isLongPmRoute = edge.sourceId === 'core:pm' && edge.targetId.startsWith('task:');
      next.push({
        ...edge,
        x1: isLongPmRoute
          ? sourceRect.left - containerRect.left
          : sourceRect.left + sourceRect.width / 2 - containerRect.left,
        y1: isLongPmRoute
          ? sourceRect.top + sourceRect.height / 2 - containerRect.top
          : sourceRect.bottom - containerRect.top,
        x2: targetRect.left + targetRect.width / 2 - containerRect.left,
        y2: targetRect.top - containerRect.top,
      });
    }
    setMeasuredEdges(next);
  }, [graph.edges]);

  useLayoutEffect(() => measureEdges(), [measureEdges]);

  useEffect(() => {
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(measureEdges);
      if (graphRef.current) observer.observe(graphRef.current);
      nodeRefs.current.forEach((node) => observer.observe(node));
      return () => observer.disconnect();
    }
    window.addEventListener('resize', measureEdges);
    return () => window.removeEventListener('resize', measureEdges);
  }, [measureEdges]);

  const renderLevel = (
    nodes: RuntimeGraphNode[],
    key: string,
    kind: 'core' | 'auxiliary' | 'task',
    index: number,
  ) => {
    const offset = kind === 'task' && nodes.length === 1
      ? (index % 2 === 0 ? 'is-offset-left' : 'is-offset-right')
      : '';
    return (
      <li
        className={`ff-agent-call-level is-${kind} is-${kind}-${index} ${nodes.length === 1 ? 'is-single' : 'is-branch'} ${offset}`}
        key={key}
      >
        <ul>
          {nodes.map((node) => (
            <AgentGraphNode
              key={node.id}
              node={node}
              selected={node.runtime?.agent_session_id === selectedAgentSessionId}
              setRef={(element) => {
                if (element) nodeRefs.current.set(node.id, element);
                else nodeRefs.current.delete(node.id);
              }}
              onSelect={onSelectAgent}
            />
          ))}
        </ul>
      </li>
    );
  };

  return (
    <section className="ff-agent-runtime-project" aria-label={`${project.title} Agent 调用图`}>
      <header className="ff-agent-call-project-header">
        <span><GitBranch aria-hidden="true" /></span>
        <div>
          <h3>{project.title}</h3>
          <p>按实际工作流阶段与子任务依赖自上而下排列</p>
        </div>
      </header>
      <div className="ff-agent-call-graph" ref={graphRef}>
        <svg className="ff-agent-call-edges" aria-hidden="true">
          <defs>
            <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
          {measuredEdges.map((edge) => {
            const isLongPmRoute = edge.sourceId === 'core:pm' && edge.targetId.startsWith('task:');
            const middleY = edge.y1 + (edge.y2 - edge.y1) / 2;
            const laneX = 8;
            const path = isLongPmRoute
              ? `M ${edge.x1} ${edge.y1} C ${laneX} ${edge.y1}, ${laneX} ${edge.y1}, ${laneX} ${edge.y1 + 24} L ${laneX} ${edge.y2 - 24} C ${laneX} ${edge.y2 - 8}, ${edge.x2} ${edge.y2 - 8}, ${edge.x2} ${edge.y2}`
              : `M ${edge.x1} ${edge.y1} C ${edge.x1} ${middleY}, ${edge.x2} ${middleY}, ${edge.x2} ${edge.y2}`;
            return (
              <g key={edge.key} className={`is-${edge.status}`}>
                <path
                  className="ff-agent-call-edge"
                  data-agent-edge={edge.key}
                  markerEnd={`url(#${markerId})`}
                  d={path}
                />
                {edge.status === 'running' && <path className="ff-agent-call-edge-flow" d={path} />}
              </g>
            );
          })}
        </svg>
        <ol className="ff-agent-call-levels">
          {graph.coreLevels.map((nodes, index) => renderLevel(nodes, `core-${index}`, 'core', index))}
          {graph.auxiliaryLevels.length > 0 && (
            <li className="ff-agent-call-stage-label"><span>辅助 Agent 调用</span></li>
          )}
          {graph.auxiliaryLevels.map((nodes, index) => renderLevel(nodes, `aux-${index}`, 'auxiliary', index))}
          {graph.taskLevels.length > 0 && (
            <li className="ff-agent-call-stage-label">
              <span>PM 拆分后的子 Agent 调用图 · {graph.taskLevels.flat().length} 个任务</span>
            </li>
          )}
          {graph.taskLevels.map((nodes, index) => renderLevel(nodes, `task-${index}`, 'task', index))}
        </ol>
      </div>
      <footer className="ff-agent-call-legend" aria-label="Agent 调用图图例">
        <span><i className="is-planned" />待调用</span>
        <span><i className="is-running" />运行中</span>
        <span><i className="is-completed" />已完成</span>
        <span><i className="is-error" />异常/阻塞</span>
      </footer>
    </section>
  );
}
