// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { AgentSpecDetail, type WorkItemPreview } from './AgentSpecDetail';
import type { AgentSpecDto, WorkItemDto } from './dto';

afterEach(cleanup);

const item: WorkItemDto = {
  id: 'task-1', parent_id: 'root-1', kind: 'TASK', title: '编写需求文档', description: null,
  summary: null, objective: '完成需求分析', status: 'todo', scope: [], exclusions: [], outputs: null,
  acceptance_criteria: null, required_skills: null, responsible_role: '产品经理', suggested_assignee: 'owner-1',
  dependency_work_item_ids: [],
};

const agentSpec: AgentSpecDto = {
  id: 'agent-spec-1', work_item_id: 'task-1', source_spec_version_id: 'spec-1', dependency_work_item_ids: [],
  created_at: '2026-09-11T00:00:00Z', content: {
    required_skills: ['requirements analysis', 'traceability'],
    allowed_tools: ['文档编辑器', '版本控制'],
    allowed_paths: ['项目文档目录'],
  },
};

function Harness() {
  const [preview, setPreview] = useState<WorkItemPreview>({ draft: '' });
  return <AgentSpecDetail
    item={item}
    agentSpecs={[agentSpec]}
    preview={preview}
    onPreviewChange={(patch) => setPreview((current) => ({ ...current, ...patch }))}
  />;
}

describe('AgentSpecDetail editable recommendations', () => {
  it('keeps defaults and supports suggested, custom and removed entries', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: '协作与依赖' }));

    expect(screen.getByText('requirements analysis')).toBeTruthy();
    expect(screen.queryByRole('combobox', { name: '新增所需技能' })).toBeNull();
    expect(screen.queryByRole('button', { name: '删除所需技能：requirements analysis' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '编辑所需技能' }));
    fireEvent.change(screen.getByRole('combobox', { name: '新增所需技能' }), { target: { value: 'test design' } });
    fireEvent.click(screen.getAllByRole('button', { name: '添加' })[0]);
    expect(screen.getByText('test design')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '删除所需技能：requirements analysis' }));
    expect(screen.queryByText('requirements analysis')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '完成编辑所需技能' }));
    expect(screen.queryByRole('combobox', { name: '新增所需技能' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '编辑允许工具' }));
    fireEvent.change(screen.getByRole('combobox', { name: '新增允许工具' }), { target: { value: 'API 调试工具' } });
    fireEvent.keyDown(screen.getByRole('combobox', { name: '新增允许工具' }), { key: 'Enter' });
    expect(screen.getByText('API 调试工具')).toBeTruthy();

    const paths = within(screen.getByRole('heading', { name: '允许路径' }).closest('section')!);
    fireEvent.click(paths.getByRole('button', { name: '编辑允许路径' }));
    fireEvent.change(paths.getByRole('combobox', { name: '新增允许路径' }), { target: { value: '自定义交付目录' } });
    fireEvent.click(paths.getByRole('button', { name: '添加' }));
    expect(paths.getByText('自定义交付目录')).toBeTruthy();
  });
});
