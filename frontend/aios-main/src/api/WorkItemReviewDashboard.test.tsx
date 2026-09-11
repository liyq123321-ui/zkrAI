// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { WorkItemReviewDashboard } from './WorkItemReviewDashboard';
import type { WorkItemDto } from './dto';

const baseItem: WorkItemDto = {
  id: 'task-review',
  parent_id: 'root-1',
  kind: 'TASK',
  title: '验收子任务',
  description: null,
  summary: null,
  objective: '交付可核验的 API',
  status: 'todo',
  scope: [],
  exclusions: [],
  outputs: null,
  acceptance_criteria: null,
  required_skills: [],
  responsible_role: 'QA Agent',
  suggested_assignee: 'qa-1',
  dependency_work_item_ids: [],
};

afterEach(cleanup);

function renderDashboard(status: string = 'todo') {
  return render(<WorkItemReviewDashboard
    item={{ ...baseItem, status }}
    content={{
      inputs: ['POST /questions', '合法的问题 JSON'],
      outputs: [
        { name: '未交付报告', format: 'Markdown', required: true },
        { name: '已验收报告', format: 'Markdown', required: true, agent_acceptance_status: 'passed', content: '# 验收证据' },
        { name: '被驳回报告', format: 'Markdown', required: true, agent_acceptance: { verdict: 'rejected' }, content: '缺少失败场景' },
      ],
      acceptance_criteria: [
        { requirement_ids: ['FR-001'], criterion: '可以提交问题', verification_method: 'API 测试', expected_result: '返回 200' },
      ],
      test_obligations: ['覆盖超时失败场景'],
      risks: [],
      open_questions: [],
    }}
    employees={[]}
    assigneeId="qa-1"
    dependencies={[]}
    workItems={[]}
  />);
}

describe('WorkItemReviewDashboard acceptance evidence', () => {
  it('shows compatible Agent acceptance states and only opens delivered artifacts', () => {
    renderDashboard();

    expect(screen.getAllByLabelText('Agent 验收：尚未交付').length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Agent 验收：验收通过').classList.contains('is-passed')).toBe(true);
    expect(screen.getByLabelText('Agent 验收：未通过').classList.contains('is-failed')).toBe(true);
    expect(screen.queryByRole('link', { name: '未交付报告' })).toBeNull();

    fireEvent.click(screen.getByRole('link', { name: '已验收报告' }));
    const evidence = screen.getByRole('region', { name: '已验收报告 详情' });
    expect(within(evidence).getByText('# 验收证据')).toBeTruthy();
  });

  it('opens assigned test cases with explicit input and expected output', () => {
    renderDashboard('in_progress');

    fireEvent.click(screen.getByRole('link', { name: 'TC-01' }));
    const testCase = screen.getByRole('region', { name: 'TC-01 详情' });
    expect(within(testCase).getByText('输入 / 前置条件')).toBeTruthy();
    expect(within(testCase).getByText(/POST \/questions/)).toBeTruthy();
    expect(within(testCase).getByText('期望输出')).toBeTruthy();
    expect(within(testCase).getByText('返回 200')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'TO-01' })).toBeTruthy();
  });

  it('projects historical completed and failed tasks without new database fields', () => {
    const { rerender } = render(<WorkItemReviewDashboard
      item={{ ...baseItem, status: 'completed' }}
      content={{ outputs: [{ name: '历史交付物', format: 'document', required: true }], acceptance_criteria: [{ criterion: '历史验收项', expected_result: '结果可复核' }] }}
      employees={[]} assigneeId="" dependencies={[]} workItems={[]}
    />);
    expect(screen.getAllByLabelText('Agent 验收：验收通过')).toHaveLength(2);
    expect(screen.getByRole('link', { name: '历史交付物' })).toBeTruthy();

    rerender(<WorkItemReviewDashboard
      item={{ ...baseItem, status: 'failed' }}
      content={{ outputs: [{ name: '失败交付物', format: 'document', required: true }] }}
      employees={[]} assigneeId="" dependencies={[]} workItems={[]}
    />);
    expect(screen.getByLabelText('Agent 验收：未通过').classList.contains('is-failed')).toBe(true);
  });
});
