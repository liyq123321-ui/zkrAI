import { describe, expect, it } from 'vitest';
import type { WorkItemDto } from './dto';
import {
  milestoneChildTasks,
  milestoneSummaryLabel,
  milestoneTaskLayers,
  milestoneTaskStats,
} from './milestoneTaskMonitoring';

function item(
  id: string,
  parentId: string | null,
  status = 'todo',
  kind: WorkItemDto['kind'] = 'TASK',
): WorkItemDto {
  return {
    id,
    parent_id: parentId,
    kind,
    title: id,
    description: null,
    objective: null,
    status,
    scope: [],
    exclusions: [],
    outputs: [],
    acceptance_criteria: [],
    required_skills: [],
    responsible_role: null,
    suggested_assignee: null,
    dependency_work_item_ids: [],
  };
}

describe('milestone task monitoring model', () => {
  it('selects only direct TASK children of the milestone', () => {
    const result = milestoneChildTasks('milestone-a', [
      item('direct', 'milestone-a'),
      item('other', 'milestone-b'),
      item('nested', 'direct'),
      item('child-milestone', 'milestone-a', 'todo', 'MILESTONE'),
    ]);

    expect(result.map(({ id }) => id)).toEqual(['direct']);
  });

  it('builds compact summaries for active, complete, and empty milestones', () => {
    const active = milestoneTaskStats([
      item('todo', 'm'),
      item('running', 'm', 'in_progress'),
      item('blocked', 'm', 'blocked'),
      item('done', 'm', 'completed'),
    ]);

    expect(active).toEqual({ total: 4, todo: 1, inProgress: 1, blocked: 1, done: 1 });
    expect(milestoneSummaryLabel(active)).toBe('4 个子任务 · 1 进行中 · 1 受阻');
    expect(milestoneSummaryLabel(milestoneTaskStats([item('done', 'm', 'done')]))).toBe('1 个子任务 · 全部完成');
    expect(milestoneSummaryLabel(milestoneTaskStats([]))).toBe('暂无子任务');
  });

  it('places parallel tasks together and dependent tasks below them', () => {
    const a = item('a', 'm');
    const b = item('b', 'm');
    const c = { ...item('c', 'm'), dependency_work_item_ids: ['a', 'b', 'outside'] };
    const d = { ...item('d', 'm'), dependency_work_item_ids: ['c'] };

    expect(milestoneTaskLayers([a, b, c, d])).toEqual([
      { depth: 0, tasks: [a, b], cycle: false },
      { depth: 1, tasks: [c], cycle: false },
      { depth: 2, tasks: [d], cycle: false },
    ]);
  });

  it('places cyclic tasks in a final anomaly layer', () => {
    const a = { ...item('a', 'm'), dependency_work_item_ids: ['b'] };
    const b = { ...item('b', 'm'), dependency_work_item_ids: ['a'] };

    expect(milestoneTaskLayers([a, b])).toEqual([
      { depth: 0, tasks: [a, b], cycle: true },
    ]);
  });
});
