// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WorkItemDto } from './dto';
import { MilestoneTaskMonitor, MilestoneTaskSummary } from './MilestoneTaskMonitor';

function task(
  id: string,
  parentId: string,
  status = 'todo',
  dependencies: string[] = [],
): WorkItemDto {
  return {
    id,
    parent_id: parentId,
    kind: 'TASK',
    title: id,
    description: null,
    objective: null,
    status,
    scope: [],
    exclusions: [],
    outputs: [],
    acceptance_criteria: [],
    required_skills: [],
    responsible_role: '开发工程师',
    suggested_assignee: 'Agent A',
    dependency_work_item_ids: dependencies,
  };
}

function milestone(id: string): WorkItemDto {
  return {
    id,
    parent_id: 'root',
    kind: 'MILESTONE',
    title: '检索能力交付',
    description: null,
    objective: '完成检索链路',
    status: 'in_progress',
    scope: [],
    exclusions: [],
    outputs: [],
    acceptance_criteria: [],
    required_skills: [],
    responsible_role: '项目经理',
    suggested_assignee: 'PM Agent',
    dependency_work_item_ids: [],
  };
}

function cyclicTasks(): WorkItemDto[] {
  return [task('a', 'm', 'todo', ['b']), task('b', 'm', 'todo', ['a'])];
}

afterEach(cleanup);

describe('MilestoneTaskMonitor', () => {
  it('renders the compact summary for direct milestone children', () => {
    render(<MilestoneTaskSummary milestoneId="m" workItems={[
      task('a', 'm', 'in_progress'),
      task('b', 'm', 'blocked'),
      task('x', 'other', 'done'),
    ]} />);

    expect(screen.getByText('2 个子任务 · 1 进行中 · 1 受阻')).toBeTruthy();
  });

  it('renders vertical layers, valid edges, statuses, and task navigation', () => {
    const onOpenWorkItem = vi.fn();
    const { container } = render(<MilestoneTaskMonitor
      milestone={milestone('m')}
      workItems={[
        task('a', 'm', 'completed'),
        task('b', 'm', 'in_progress'),
        task('c', 'm', 'blocked', ['a', 'b', 'outside']),
        task('x', 'other', 'todo'),
      ]}
      onOpenWorkItem={onOpenWorkItem}
    />);

    expect(screen.getByRole('heading', { name: '子任务执行监控' })).toBeTruthy();
    expect(screen.getByRole('group', { name: '执行层 1' })).toBeTruthy();
    expect(screen.getByRole('group', { name: '执行层 2' })).toBeTruthy();
    expect(container.querySelector('[data-milestone-edge="a->c"]')).toBeTruthy();
    expect(container.querySelector('[data-milestone-edge="b->c"]')).toBeTruthy();
    expect(container.querySelector('[data-milestone-edge="outside->c"]')).toBeNull();
    expect(within(screen.getByRole('button', { name: '打开子任务：a，状态：已完成' })).getByText('已完成')).toBeTruthy();
    expect(within(screen.getByRole('button', { name: '打开子任务：b，状态：进行中' })).getByText('进行中')).toBeTruthy();
    expect(within(screen.getByRole('button', { name: '打开子任务：c，状态：受阻' })).getByText('受阻')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '打开子任务：c，状态：受阻' }));
    expect(onOpenWorkItem).toHaveBeenCalledWith('c');
  });

  it('shows empty and cyclic-dependency messages', () => {
    const { rerender } = render(<MilestoneTaskMonitor
      milestone={milestone('m')}
      workItems={[]}
      onOpenWorkItem={vi.fn()}
    />);
    expect(screen.getByText('该里程碑暂无子任务')).toBeTruthy();

    rerender(<MilestoneTaskMonitor
      milestone={milestone('m')}
      workItems={cyclicTasks()}
      onOpenWorkItem={vi.fn()}
    />);
    expect(screen.getByText('检测到循环依赖，以下任务无法确定执行顺序。')).toBeTruthy();
  });
});
