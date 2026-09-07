import type { WorkItemDto } from './dto';
import { workItemLane } from './workflowUi';

export interface MilestoneTaskStats {
  total: number;
  todo: number;
  inProgress: number;
  blocked: number;
  done: number;
}

export interface MilestoneTaskLayer {
  depth: number;
  tasks: WorkItemDto[];
  cycle: boolean;
}

export function milestoneChildTasks(
  milestoneId: string,
  workItems: WorkItemDto[],
): WorkItemDto[] {
  return workItems.filter((item) => item.kind === 'TASK' && item.parent_id === milestoneId);
}

export function milestoneTaskStats(tasks: WorkItemDto[]): MilestoneTaskStats {
  return tasks.reduce<MilestoneTaskStats>((stats, task) => {
    stats.total += 1;
    const lane = workItemLane(task.status);
    if (task.status?.trim().toLowerCase() === 'blocked' || lane === 'failed') {
      stats.blocked += 1;
    } else if (lane === 'done') {
      stats.done += 1;
    } else if (lane === 'in_progress') {
      stats.inProgress += 1;
    } else {
      stats.todo += 1;
    }
    return stats;
  }, { total: 0, todo: 0, inProgress: 0, blocked: 0, done: 0 });
}

export function milestoneSummaryLabel(stats: MilestoneTaskStats): string {
  if (stats.total === 0) return '暂无子任务';
  if (stats.done === stats.total) return `${stats.total} 个子任务 · 全部完成`;
  const parts = [`${stats.total} 个子任务`];
  if (stats.inProgress > 0) parts.push(`${stats.inProgress} 进行中`);
  if (stats.blocked > 0) parts.push(`${stats.blocked} 受阻`);
  return parts.join(' · ');
}

export function milestoneTaskLayers(tasks: WorkItemDto[]): MilestoneTaskLayer[] {
  const taskIds = new Set(tasks.map(({ id }) => id));
  const dependencies = new Map(tasks.map((task) => [
    task.id,
    task.dependency_work_item_ids.filter((id) => taskIds.has(id)),
  ]));
  const unresolved = new Set(tasks.map(({ id }) => id));
  const depths = new Map<string, number>();

  while (unresolved.size > 0) {
    const ready = tasks.filter((task) => unresolved.has(task.id)
      && (dependencies.get(task.id) ?? []).every((id) => depths.has(id)));
    if (ready.length === 0) break;
    for (const task of ready) {
      const parents = dependencies.get(task.id) ?? [];
      depths.set(task.id, parents.length === 0
        ? 0
        : Math.max(...parents.map((id) => depths.get(id) ?? 0)) + 1);
      unresolved.delete(task.id);
    }
  }

  const grouped = new Map<number, WorkItemDto[]>();
  for (const task of tasks) {
    const depth = depths.get(task.id);
    if (depth === undefined) continue;
    grouped.set(depth, [...(grouped.get(depth) ?? []), task]);
  }
  const layers = [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(([depth, layerTasks]) => ({ depth, tasks: layerTasks, cycle: false }));

  if (unresolved.size > 0) {
    layers.push({
      depth: layers.length === 0 ? 0 : layers[layers.length - 1].depth + 1,
      tasks: tasks.filter((task) => unresolved.has(task.id)),
      cycle: true,
    });
  }
  return layers;
}
