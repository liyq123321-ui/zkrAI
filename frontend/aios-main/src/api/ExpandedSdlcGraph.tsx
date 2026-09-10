import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { AlertTriangle, CheckCircle2, Clock3, GitFork } from 'lucide-react';
import type { WorkItemDto } from './dto';
import { milestoneTaskLayers, milestoneTaskStats } from './milestoneTaskMonitoring';
import type { TopLevelPhase } from './TopLevelGraph';
import { workItemLane, workItemStatusLabel } from './workflowUi';

type GraphEdge = { key: string; x1: number; y1: number; x2: number; y2: number; crossPhase: boolean };

function statusClass(task: WorkItemDto): 'todo' | 'active' | 'done' | 'blocked' {
  const normalized = task.status?.trim().toLowerCase();
  if (normalized === 'blocked' || workItemLane(task.status) === 'failed') return 'blocked';
  if (workItemLane(task.status) === 'done') return 'done';
  if (workItemLane(task.status) === 'in_progress') return 'active';
  return 'todo';
}

function assignee(task: WorkItemDto): string {
  return task.suggested_assignee || task.responsible_role || '待分配 Agent';
}

export function ExpandedSdlcGraph({ phases, onOpenWorkItem }: {
  phases: TopLevelPhase[];
  onOpenWorkItem: (workItemId: string) => void;
}) {
  const graphRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const markerId = `ff-sdlc-expanded-arrow-${useId().replaceAll(':', '')}`;
  const tasks = useMemo(() => phases.flatMap((phase) => phase.tasks), [phases]);
  const taskIds = useMemo(() => new Set(tasks.map((task) => task.id)), [tasks]);
  const phaseByTask = useMemo(() => new Map(phases.flatMap((phase) => phase.tasks.map((task) => [task.id, phase.stage_id] as const))), [phases]);

  const measureEdges = useCallback(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const graphRect = graph.getBoundingClientRect();
    const next: GraphEdge[] = [];
    for (const task of tasks) {
      const target = nodeRefs.current.get(task.id);
      if (!target) continue;
      const targetRect = target.getBoundingClientRect();
      for (const dependencyId of task.dependency_work_item_ids) {
        if (!taskIds.has(dependencyId)) continue;
        const source = nodeRefs.current.get(dependencyId);
        if (!source) continue;
        const sourceRect = source.getBoundingClientRect();
        next.push({
          key: `${dependencyId}->${task.id}`,
          x1: sourceRect.left + sourceRect.width / 2 - graphRect.left,
          y1: sourceRect.bottom - graphRect.top,
          x2: targetRect.left + targetRect.width / 2 - graphRect.left,
          y2: targetRect.top - graphRect.top,
          crossPhase: phaseByTask.get(dependencyId) !== phaseByTask.get(task.id),
        });
      }
    }
    setEdges(next);
  }, [phaseByTask, taskIds, tasks]);

  useLayoutEffect(() => measureEdges(), [measureEdges]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(measureEdges);
    const afterEntrance = window.setTimeout(measureEdges, 720);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(afterEntrance);
    };
  }, [measureEdges]);
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

  return <div className="ff-expanded-sdlc-graph" ref={graphRef}>
    <svg className="ff-expanded-sdlc-edges" aria-hidden="true">
      <defs><marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      {edges.map((edge) => {
        const middleY = (edge.y1 + edge.y2) / 2;
        return <path key={edge.key} className={edge.crossPhase ? 'is-cross-phase' : ''} markerEnd={`url(#${markerId})`} d={`M ${edge.x1} ${edge.y1} C ${edge.x1} ${middleY}, ${edge.x2} ${middleY}, ${edge.x2} ${edge.y2}`} />;
      })}
    </svg>
    {phases.map((phase, phaseIndex) => {
      const stats = milestoneTaskStats(phase.tasks);
      const layers = milestoneTaskLayers(phase.tasks);
      return <section
        key={`${phase.iteration}:${phase.stage_id}`}
        className={`ff-expanded-sdlc-phase is-${phase.status}`}
        style={{ '--ff-phase-order': phaseIndex } as CSSProperties}
      >
        <header>
          <span>阶段 {phase.stage.number}</span>
          <div><h3>{phase.stage.name}</h3><p>{phase.stage.objective}</p></div>
          <dl><div><dt>任务</dt><dd>{stats.total}</dd></div><div><dt>完成</dt><dd>{stats.done}</dd></div><div><dt>进行中</dt><dd>{stats.inProgress}</dd></div><div><dt>受阻</dt><dd>{stats.blocked}</dd></div></dl>
        </header>
        <div className="ff-expanded-sdlc-phase-body">
          {layers.map((layer) => <div key={`${layer.depth}:${layer.cycle}`} className={`ff-expanded-sdlc-layer${layer.cycle ? ' is-cycle' : ''}`}>
            <span>{layer.cycle ? '依赖异常' : `执行层 ${layer.depth + 1}`}</span>
            <div>{layer.tasks.map((task, taskIndex) => {
              const state = statusClass(task);
              return <button
                key={task.id}
                ref={(node) => { if (node) nodeRefs.current.set(task.id, node); else nodeRefs.current.delete(task.id); }}
                type="button"
                className={`ff-milestone-node is-${state}`}
                style={{ '--ff-task-order': layer.depth * 2 + taskIndex } as CSSProperties}
                onClick={() => onOpenWorkItem(task.id)}
                aria-label={`打开子任务：${task.title || task.id}，状态：${workItemStatusLabel(task.status)}`}
              >
                <span className="ff-milestone-node-icon">{state === 'done' ? <CheckCircle2 aria-hidden="true" /> : state === 'blocked' ? <AlertTriangle aria-hidden="true" /> : <Clock3 aria-hidden="true" />}</span>
                <span className="ff-milestone-node-copy"><strong>{task.title || task.id}</strong><small>#{task.id} · {assignee(task)}</small></span>
                <span className={`ff-milestone-node-status is-${state}`}>{workItemStatusLabel(task.status)}</span>
                {task.dependency_work_item_ids.length > 0 && <em>依赖 {task.dependency_work_item_ids.length}</em>}
              </button>;
            })}</div>
          </div>)}
          {!phase.tasks.length && <div className="ff-expanded-sdlc-empty"><GitFork aria-hidden="true" />该阶段尚未分配子任务</div>}
        </div>
        {phaseIndex < phases.length - 1 && <span className="ff-expanded-sdlc-stage-link" aria-hidden="true" />}
      </section>;
    })}
  </div>;
}
