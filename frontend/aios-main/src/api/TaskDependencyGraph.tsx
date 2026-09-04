import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { GitFork } from 'lucide-react';
import type { WorkItemDto } from './dto';

export type TaskDagProject = {
  id: string;
  title: string;
  tasks: WorkItemDto[];
};

type TaskDagEdge = {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

function taskDepth(task: WorkItemDto) {
  return Number.isInteger(task.graph_depth) && (task.graph_depth ?? 0) >= 0
    ? task.graph_depth ?? 0
    : 0;
}

function taskAssignee(task: WorkItemDto) {
  return task.suggested_assignee || task.responsible_role || '待分配 Agent';
}

function TaskProjectSwimlane({ project, onOpenWorkItem }: {
  project: TaskDagProject;
  onOpenWorkItem: (workItemId: string) => void;
}) {
  const graphRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());
  const [edges, setEdges] = useState<TaskDagEdge[]>([]);
  const markerId = `ff-task-arrow-${useId().replaceAll(':', '')}`;
  const taskIds = useMemo(() => new Set(project.tasks.map((task) => task.id)), [project.tasks]);
  const depthColumns = useMemo(() => {
    const grouped = new Map<number, WorkItemDto[]>();
    for (const task of project.tasks) {
      const depth = taskDepth(task);
      grouped.set(depth, [...(grouped.get(depth) ?? []), task]);
    }
    return [...grouped.entries()].sort(([left], [right]) => left - right);
  }, [project.tasks]);

  const measureEdges = useCallback(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const graphRect = graph.getBoundingClientRect();
    const nextEdges: TaskDagEdge[] = [];
    for (const task of project.tasks) {
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
          x1: sourceRect.right - graphRect.left,
          y1: sourceRect.top + sourceRect.height / 2 - graphRect.top,
          x2: targetRect.left - graphRect.left,
          y2: targetRect.top + targetRect.height / 2 - graphRect.top,
        });
      }
    }
    setEdges(nextEdges);
  }, [project.tasks, taskIds]);

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

  return (
    <section className="ff-task-dag-project" aria-label={`${project.title}任务依赖图`}>
      <header className="ff-task-dag-project-header">
        <span><GitFork aria-hidden="true" /></span>
        <div><strong>{project.title}</strong><small>{project.tasks.length} 个子任务</small></div>
      </header>
      <div className="ff-task-dag-depths" ref={graphRef}>
        <svg className="ff-task-dag-edges" aria-hidden="true">
          <defs>
            <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
          {edges.map((edge) => {
            const middleX = (edge.x1 + edge.x2) / 2;
            return (
              <path
                key={edge.key}
                className="ff-task-dag-edge"
                data-dependency-edge={edge.key}
                markerEnd={`url(#${markerId})`}
                d={`M ${edge.x1} ${edge.y1} C ${middleX} ${edge.y1}, ${middleX} ${edge.y2}, ${edge.x2} ${edge.y2}`}
              />
            );
          })}
        </svg>
        {depthColumns.length === 0 && <div className="ff-task-dag-project-empty">暂无子任务</div>}
        {depthColumns.map(([depth, tasks]) => (
          <div key={depth} className="ff-task-dag-depth" role="group" aria-label={`执行层 ${depth}`}>
            <span className="ff-task-dag-depth-label">DEPTH {depth}</span>
            <div className="ff-task-dag-depth-nodes">
              {tasks.map((task) => (
                <button
                  key={task.id}
                  ref={(node) => {
                    if (node) nodeRefs.current.set(task.id, node);
                    else nodeRefs.current.delete(task.id);
                  }}
                  type="button"
                  className="ff-task-dag-node"
                  onClick={() => onOpenWorkItem(task.id)}
                >
                  <span className="ff-task-dag-node-icon"><GitFork aria-hidden="true" /></span>
                  <span className="ff-task-dag-node-copy">
                    <strong>{task.title || task.id}</strong>
                    <small>#{task.id} · {taskAssignee(task)}</small>
                  </span>
                  {task.dependency_work_item_ids.length > 0 && <em>依赖 {task.dependency_work_item_ids.length}</em>}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function TaskDependencyGraph({ projects, onOpenWorkItem }: {
  projects: TaskDagProject[];
  onOpenWorkItem: (workItemId: string) => void;
}) {
  if (projects.length === 0) return <div className="ff-task-dag-empty">等待后端生成子任务</div>;
  return (
    <section className="ff-task-dag" aria-label="子任务依赖图">
      {projects.map((project) => (
        <TaskProjectSwimlane key={project.id} project={project} onOpenWorkItem={onOpenWorkItem} />
      ))}
    </section>
  );
}
