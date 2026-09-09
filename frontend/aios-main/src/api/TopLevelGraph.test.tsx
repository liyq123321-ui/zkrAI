// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { AgentSpecDto, WorkItemDto } from './dto';
import { buildTopLevelPlan, TopLevelGraph } from './TopLevelGraph';

const phases = [
  { milestone_key: 'requirements', stage_id: 'requirements', iteration: 0, exit_gate_key: 'requirements-gate', schedule: '第 1 周' },
  { milestone_key: 'design', stage_id: 'design', iteration: 0, exit_gate_key: 'design-gate', schedule: '第 2 周' },
];

const stages = [
  {
    id: 'requirements', number: 1, name: '需求定审', scope: 'project', objective: '冻结需求基线',
    entry_criteria: '已提交初始需求', exit_criteria: '需求评审通过', owner: '产品经理',
    schedule_guidance: '15%~20%', deliverables: [{ id: 'requirements.d1', name: '《需求规格说明书》' }],
  },
  {
    id: 'design', number: 2, name: '设计签审', scope: 'project', objective: '完成系统设计',
    entry_criteria: '需求基线通过', exit_criteria: '设计评审通过', owner: '架构师',
    schedule_guidance: '15%~20%', deliverables: [{ id: 'design.d1', name: '《概要设计文档》' }],
  },
];

function spec(taskKey: string, milestoneKey: string): AgentSpecDto {
  return {
    id: `spec-${taskKey}`, work_item_id: taskKey, source_spec_version_id: 'prd-1',
    dependency_work_item_ids: [], created_at: '2026-09-09T00:00:00Z',
    content: {
      sdlc: {
        model: 'strict_waterfall', rules_version: '1', rules_hash: 'abcdef1234567890',
        selection_reason: '强监管且要求逐阶段正式签审', phases: [...phases].reverse(),
        task: { task_key: taskKey, activity_ids: [], deliverable_ids: [] },
        model_rules: { id: 'strict_waterfall', name: '纯线性瀑布（强门禁·合规型）', stages },
        milestone_key: milestoneKey,
      },
    },
  };
}

const workItems: WorkItemDto[] = [
  { id: 'm-req', local_key: 'requirements', parent_id: 'root', kind: 'MILESTONE', title: '需求定审', description: null, summary: null, objective: '冻结需求基线', scope: null, exclusions: null, outputs: null, acceptance_criteria: null, required_skills: null, responsible_role: null, suggested_assignee: null, dependency_work_item_ids: [] },
  { id: 'requirements-work', local_key: 'requirements-work', parent_id: 'm-req', kind: 'TASK', title: '编写需求', status: 'completed', description: null, summary: null, objective: null, scope: null, exclusions: null, outputs: null, acceptance_criteria: null, required_skills: null, responsible_role: null, suggested_assignee: null, dependency_work_item_ids: [] },
  { id: 'requirements-gate', local_key: 'requirements-gate', parent_id: 'm-req', kind: 'TASK', title: '需求评审', status: 'completed', description: null, summary: null, objective: null, scope: null, exclusions: null, outputs: null, acceptance_criteria: null, required_skills: null, responsible_role: null, suggested_assignee: null, dependency_work_item_ids: ['requirements-work'] },
  { id: 'm-design', local_key: 'design', parent_id: 'root', kind: 'MILESTONE', title: '设计签审', description: null, summary: null, objective: '完成系统设计', scope: null, exclusions: null, outputs: null, acceptance_criteria: null, required_skills: null, responsible_role: null, suggested_assignee: null, dependency_work_item_ids: [] },
  { id: 'design-work', local_key: 'design-work', parent_id: 'm-design', kind: 'TASK', title: '系统设计', status: 'in_progress', description: null, summary: null, objective: null, scope: null, exclusions: null, outputs: null, acceptance_criteria: null, required_skills: null, responsible_role: null, suggested_assignee: null, dependency_work_item_ids: ['requirements-gate'] },
  { id: 'design-gate', local_key: 'design-gate', parent_id: 'm-design', kind: 'TASK', title: '设计评审', status: 'todo', description: null, summary: null, objective: null, scope: null, exclusions: null, outputs: null, acceptance_criteria: null, required_skills: null, responsible_role: null, suggested_assignee: null, dependency_work_item_ids: ['design-work'] },
];

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.open = true; };
  HTMLDialogElement.prototype.close = function close() { this.open = false; };
});

afterEach(cleanup);

describe('TopLevelGraph', () => {
  it('builds a vertical phase view and opens phase task relationships in a dialog', () => {
    const agentSpecs = [spec('requirements-work', 'requirements'), spec('design-work', 'design')];
    const plan = buildTopLevelPlan(agentSpecs, workItems)!;
    expect(plan.modelName).toBe('纯线性瀑布（强门禁·合规型）');
    expect(plan.phases.map((phase) => phase.status)).toEqual(['complete', 'active']);

    const onOpen = vi.fn();
    render(<TopLevelGraph projectTitle="支付平台" agentSpecs={agentSpecs} workItems={workItems} onOpenWorkItem={onOpen} />);
    const graph = screen.getByRole('region', { name: '支付平台顶层阶段图' });
    const cards = within(graph).getAllByRole('button');
    expect(cards.map((card) => card.querySelector('h3')?.textContent)).toEqual(['需求定审', '设计签审']);
    expect(within(graph).getByText('2/2 项任务完成')).toBeTruthy();
    expect(within(graph).getByText('0/2 项任务完成')).toBeTruthy();
    expect(within(graph).getByText('《概要设计文档》')).toBeTruthy();
    fireEvent.click(cards[1]);
    const dialog = screen.getByRole('dialog', { name: 'SDLC 阶段详情' });
    expect(within(dialog).getByRole('heading', { name: '子任务执行监控' })).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: /打开子任务：系统设计/ })).toBeTruthy();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('shows an honest empty state before lifecycle decomposition exists', () => {
    render(<TopLevelGraph projectTitle="新项目" agentSpecs={[]} workItems={[]} onOpenWorkItem={vi.fn()} />);
    expect(screen.getByRole('status').textContent).toContain('尚未生成顶层阶段图');
  });
});
