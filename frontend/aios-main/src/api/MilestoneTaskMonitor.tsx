import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { AlertTriangle, CheckCircle2, Clock3, GitFork, ListChecks } from 'lucide-react';
import type { WorkItemDto } from './dto';
import {
  milestoneChildTasks,
  milestoneSummaryLabel,
  milestoneTaskLayers,
  milestoneTaskStats,
} from './milestoneTaskMonitoring';
import { workItemLane, workItemStatusLabel } from './workflowUi';

interface MilestoneTaskEdge {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

function taskAssignee(task: WorkItemDto): string {
  return task.suggested_assignee || task.responsible_role || '待分配 Agent';
}

function taskStatusClass(task: WorkItemDto): 'todo' | 'active' | 'done' | 'blocked' {
  const normalized = task.status?.trim().toLowerCase();
  if (normalized === 'blocked' || workItemLane(task.status) === 'failed') return 'blocked';
  if (workItemLane(task.status) === 'done') return 'done';
  if (workItemLane(task.status) === 'in_progress') return 'active';
  return 'todo';
}

export function MilestoneTaskSummary({ milestoneId, workItems }: {
  milestoneId: string;
  workItems: WorkItemDto[];
}) {
  const stats = milestoneTaskStats(milestoneChildTasks(milestoneId, workItems));
  const summary = milestoneSummaryLabel(stats);
  const modifier = stats.blocked > 0
    ? ' is-blocked'
    : stats.total > 0 && stats.done === stats.total
      ? ' is-complete'
      : '';

  return (
    <div className={`ff-milestone-summary${modifier}`} aria-label={`子任务状态：${summary}`}>
      <GitFork aria-hidden="true" />
      <span>{summary}</span>
    </div>
  );
}

export function MilestoneTaskMonitor({ milestone, workItems, onOpenWorkItem }: {
  milestone: WorkItemDto;
  workItems: WorkItemDto[];
  onOpenWorkItem: (workItemId: string) => void;
}) {
  const graphRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());
  const [edges, setEdges] = useState<MilestoneTaskEdge[]>([]);
  const markerId = `ff-milestone-arrow-${useId().replaceAll(':', '')}`;
  const tasks = useMemo(
    () => milestoneChildTasks(milestone.id, workItems),
    [milestone.id, workItems],
  );
  const stats = useMemo(() => milestoneTaskStats(tasks), [tasks]);
  const layers = useMemo(() => milestoneTaskLayers(tasks), [tasks]);
  const taskIds = useMemo(() => new Set(tasks.map(({ id }) => id)), [tasks]);

  const measureEdges = useCallback(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const graphRect = graph.getBoundingClientRect();
    const nextEdges: MilestoneTaskEdge[] = [];
    for (const task of tasks) {
      const target = nodeRefs.current.get(task.id);
      if (!target) continue;
      const targetRect = target.getBoundingClientRect();
      for (const dependencyId of task.dependency_work_item_ids) {
        if (!taskIds.has(dependencyId)) continue;
        const source = nodeRefs.current.get(dependencyId);
        if (!source) continue;
        const sourceRect = source.getBoundingClientRect();
        nextEdges.push({
          key: `${dependencyId}->${task.id}`,
          x1: sourceRect.left + sourceRect.width / 2 - graphRect.left,
          y1: sourceRect.bottom - graphRect.top,
          x2: targetRect.left + targetRect.width / 2 - graphRect.left,
          y2: targetRect.top - graphRect.top,
        });
      }
    }
    setEdges(nextEdges);
  }, [taskIds, tasks]);

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

  const milestoneLane = workItemLane(milestone.status);

  return (
    <article className="ff-milestone-monitor" aria-label={`${milestone.title || milestone.id}子任务监控`}>
      <div className="ff-spec-titlebar">
        <div className="ff-spec-title-tags">
          <span className="ff-item-id">#{milestone.id}</span>
          <span className="ff-priority">P1</span>
          <span className={`ff-detail-status is-${milestoneLane}`}>{workItemStatusLabel(milestone.status)}</span>
        </div>
        <div>
          <h2>{milestone.title || milestone.id}</h2>
          <p>Milestone · 子任务状态与依赖关系实时呈现</p>
        </div>
      </div>

      <div className="ff-spec-objective">
        <span><ListChecks aria-hidden="true" /></span>
        <div><h3>里程碑目标</h3><p>{milestone.objective || milestone.description || '未提供里程碑目标'}</p></div>
      </div>

      <section className="ff-milestone-overview" aria-label="里程碑任务概览">
        <div><span>总任务</span><strong>{stats.total}</strong></div>
        <div><span>已完成</span><strong>{stats.done}</strong></div>
        <div><span>进行中</span><strong>{stats.inProgress}</strong></div>
        <div className={stats.blocked > 0 ? 'is-blocked' : ''}><span>受阻</span><strong>{stats.blocked}</strong></div>
      </section>

      <section className="ff-milestone-monitor-section">
        <header>
          <span><GitFork aria-hidden="true" /></span>
          <div>
            <h2>子任务执行监控</h2>
            <p>同层任务可并行执行，箭头表示前置依赖，执行顺序由上至下。</p>
          </div>
        </header>

        {tasks.length === 0 ? (
          <div className="ff-milestone-empty">该里程碑暂无子任务</div>
        ) : (
          <div className="ff-milestone-graph-scroll">
            <div className="ff-milestone-graph" ref={graphRef}>
              <svg className="ff-milestone-edges" aria-hidden="true">
                <defs>
                  <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                    <path d="M 0 0 L 10 5 L 0 10 z" />
                  </marker>
                </defs>
                {edges.map((edge) => {
                  const middleY = (edge.y1 + edge.y2) / 2;
                  return (
                    <path
                      key={edge.key}
                      className="ff-milestone-edge"
                      data-milestone-edge={edge.key}
                      markerEnd={`url(#${markerId})`}
                      d={`M ${edge.x1} ${edge.y1} C ${edge.x1} ${middleY}, ${edge.x2} ${middleY}, ${edge.x2} ${edge.y2}`}
                    />
                  );
                })}
              </svg>

              {layers.map((layer) => (
                <div
                  key={`${layer.depth}:${layer.cycle ? 'cycle' : 'normal'}`}
                  className={`ff-milestone-layer${layer.cycle ? ' is-cycle' : ''}`}
                  role="group"
                  aria-label={layer.cycle ? '依赖异常' : `执行层 ${layer.depth + 1}`}
                >
                  {layer.cycle ? (
                    <div className="ff-milestone-cycle-warning">
                      <AlertTriangle aria-hidden="true" />
                      检测到循环依赖，以下任务无法确定执行顺序。
                    </div>
                  ) : (
                    <span className="ff-milestone-layer-label">执行层 {layer.depth + 1}</span>
                  )}
                  <div className="ff-milestone-layer-nodes">
                    {layer.tasks.map((task) => {
                      const statusClass = taskStatusClass(task);
                      const localDependencies = task.dependency_work_item_ids.filter((id) => taskIds.has(id));
                      return (
                        <button
                          key={task.id}
                          ref={(node) => {
                            if (node) nodeRefs.current.set(task.id, node);
                            else nodeRefs.current.delete(task.id);
                          }}
                          type="button"
                          className={`ff-milestone-node is-${statusClass}`}
                          aria-label={`打开子任务：${task.title || task.id}`}
                          onClick={() => onOpenWorkItem(task.id)}
                        >
                          <span className="ff-milestone-node-icon">
                            {statusClass === 'done' ? <CheckCircle2 aria-hidden="true" /> : statusClass === 'blocked' ? <AlertTriangle aria-hidden="true" /> : <Clock3 aria-hidden="true" />}
                          </span>
                          <span className="ff-milestone-node-copy">
                            <strong>{task.title || task.id}</strong>
                            <small>#{task.id} · {taskAssignee(task)}</small>
                          </span>
                          <span className={`ff-milestone-node-status is-${statusClass}`}>{workItemStatusLabel(task.status)}</span>
                          {localDependencies.length > 0 && <em>依赖 {localDependencies.length}</em>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>
    </article>
  );
}
