// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import type { WorkItemDto } from './dto';
import { defaultWorkItemFilters, filterWorkItems } from './workItemFilters';

const items = [
  { id: 'root-a', kind: 'ROOT', title: '知识问答', parent_id: null, dependency_work_item_ids: [], suggested_assignee: 'Owner' },
  { id: 'task-a', kind: 'TASK', title: '构建检索流程', objective: '接入向量检索', parent_id: 'root-a', dependency_work_item_ids: [], suggested_assignee: 'AI Agent' },
  { id: 'wrong-root', kind: 'TASK', title: '向量检索异地副本', parent_id: 'root-b', dependency_work_item_ids: [], suggested_assignee: 'AI Agent' },
  { id: 'wrong-kind', kind: 'MILESTONE', title: '向量检索里程碑', parent_id: 'root-a', dependency_work_item_ids: [], suggested_assignee: 'AI Agent' },
  { id: 'wrong-agent', kind: 'TASK', title: '向量检索后端任务', parent_id: 'root-a', dependency_work_item_ids: [], suggested_assignee: 'Backend Agent' },
  { id: 'wrong-search', kind: 'TASK', title: '客服接口', objective: '处理客户工单', parent_id: 'root-a', dependency_work_item_ids: [], suggested_assignee: 'AI Agent' },
] as WorkItemDto[];

const roots = new Map([
  ['root-a', 'root-a'],
  ['task-a', 'root-a'],
  ['wrong-root', 'root-b'],
  ['wrong-kind', 'root-a'],
  ['wrong-agent', 'root-a'],
  ['wrong-search', 'root-a'],
]);
const assignee = (item: WorkItemDto) => item.suggested_assignee || '待分配 Agent';

describe('filterWorkItems', () => {
  it('shows every WorkItem with default filters', () => {
    expect(filterWorkItems(items, defaultWorkItemFilters(), roots, assignee).map((item) => item.id))
      .toEqual(['root-a', 'task-a', 'wrong-root', 'wrong-kind', 'wrong-agent', 'wrong-search']);
  });

  it('excludes a dedicated decoy for each root, kind, assignee, and text filter', () => {
    expect(filterWorkItems(items, {
      search: '向量', selectedRootIds: ['root-a'], kind: 'TASK', agent: 'AI Agent',
    }, roots, assignee).map((item) => item.id)).toEqual(['task-a']);
  });

  it('searches a valid displayed ROOT summary but ignores invalid runtime summaries', () => {
    const summaries = [
      { ...items[0], summary: '本地温度换算器' },
      { ...items[1], summary: '不可用\n摘要' },
    ];

    expect(filterWorkItems(summaries, {
      ...defaultWorkItemFilters(), search: '温度换算',
    }, roots, assignee).map((item) => item.id)).toEqual(['root-a']);
    expect(filterWorkItems(summaries, {
      ...defaultWorkItemFilters(), search: '不可用',
    }, roots, assignee)).toEqual([]);
  });
});
