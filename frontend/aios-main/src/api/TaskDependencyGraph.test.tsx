// @vitest-environment jsdom
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { WorkItemDto } from './dto';
import { TaskDependencyGraph } from './TaskDependencyGraph';

function task(id: string, depth: number, dependencies: string[]): WorkItemDto {
  return {
    id,
    parent_id: 'root-a',
    kind: 'TASK',
    title: `任务 ${id}`,
    description: null,
    objective: null,
    status: 'todo',
    scope: [],
    exclusions: [],
    outputs: [],
    acceptance_criteria: [],
    required_skills: [],
    responsible_role: '开发工程师',
    suggested_assignee: 'Agent A',
    dependency_work_item_ids: dependencies,
    available_actions: [],
    graph_depth: depth,
  };
}

describe('TaskDependencyGraph', () => {
  it('renders project-isolated depth columns and opens the selected task', () => {
    const onOpenWorkItem = vi.fn();
    const { container } = render(
      <TaskDependencyGraph
        projects={[
          {
            id: 'project-a',
            title: '知识问答',
            tasks: [task('a', 0, []), task('b', 1, ['a']), task('c', 2, ['b'])],
          },
          {
            id: 'project-b',
            title: '客服助手',
            tasks: [task('x', 0, ['a'])],
          },
          { id: 'project-c', title: '空项目', tasks: [] },
        ]}
        onOpenWorkItem={onOpenWorkItem}
      />,
    );

    const projectA = screen.getByRole('region', { name: '知识问答任务依赖图' });
    expect(within(projectA).getByRole('group', { name: '执行层 0' })).toBeTruthy();
    expect(within(projectA).getByRole('group', { name: '执行层 1' })).toBeTruthy();
    expect(within(projectA).getByRole('group', { name: '执行层 2' })).toBeTruthy();
    expect(container.querySelectorAll('[data-dependency-edge]')).toHaveLength(2);
    expect(container.querySelector('[data-dependency-edge="a->x"]')).toBeNull();
    expect(within(screen.getByRole('region', { name: '空项目任务依赖图' })).getByText('暂无子任务')).toBeTruthy();

    fireEvent.click(within(projectA).getByRole('button', { name: /任务 b/ }));
    expect(onOpenWorkItem).toHaveBeenCalledWith('b');
  });
});
