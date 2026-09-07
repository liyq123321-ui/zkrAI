// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import type { WorkItemDto } from './dto';
import { defaultWorkItemFilters, filterWorkItems } from './workItemFilters';

const items = [
  { id: 'root-a', kind: 'ROOT', title: '知识问答', parent_id: null, dependency_work_item_ids: [], suggested_assignee: 'Owner' },
  { id: 'task-a', kind: 'TASK', title: '构建检索流程', objective: '接入向量检索', parent_id: 'root-a', dependency_work_item_ids: [], suggested_assignee: 'AI Agent' },
  { id: 'task-b', kind: 'TASK', title: '客服接口', objective: '处理客户工单', parent_id: 'root-b', dependency_work_item_ids: [], suggested_assignee: 'Backend Agent' },
] as WorkItemDto[];

const roots = new Map([['root-a', 'root-a'], ['task-a', 'root-a'], ['task-b', 'root-b']]);
const assignee = (item: WorkItemDto) => item.suggested_assignee || '待分配 Agent';

describe('filterWorkItems', () => {
  it('shows every WorkItem with default filters', () => {
    expect(filterWorkItems(items, defaultWorkItemFilters(), roots, assignee).map((item) => item.id))
      .toEqual(['root-a', 'task-a', 'task-b']);
  });

  it('intersects root, kind, assignee, and text filters', () => {
    expect(filterWorkItems(items, {
      search: '向量', selectedRootIds: ['root-a'], kind: 'TASK', agent: 'AI Agent',
    }, roots, assignee).map((item) => item.id)).toEqual(['task-a']);
  });
});
