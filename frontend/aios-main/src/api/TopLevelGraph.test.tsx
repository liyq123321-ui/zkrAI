// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { AgentSpecDto, SpecVersionDto, WorkItemDto } from './dto';
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
      required_skills: taskKey === 'design-work' ? ['React architecture', 'frontend design'] : ['traceability'],
      acceptance_criteria: [{
        requirement_ids: ['FR-001'], criterion: `${taskKey} 完成后验证 FR-001`,
        verification_method: '检查任务产物', expected_result: '满足需求',
      }],
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

const sourceSpec: SpecVersionDto = {
  id: 'prd-1', project_id: 'project-1', revision: 1, generation_source: 'PM_AGENT', parent_version_id: null,
  change_summary: 'Initial specification', status: 'APPROVED', reviews: [], created_at: '2026-09-09T00:00:00Z', markdown: '',
  content: {
    functional_requirements: [
      { requirement_id: 'FR-001', statement: '必须生成可下载的验收报告', priority: 'MUST' },
      { requirement_id: 'FR-002', statement: '必须保留独立的审计记录', priority: 'SHOULD' },
    ],
    non_functional_requirements: [],
    acceptance_criteria: [{ requirement_ids: ['FR-001'], criterion: '验收报告包含全部测试结果', verification_method: '运行测试套件', expected_result: '全部通过' }],
  },
};

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
    const cards = within(graph).getAllByRole('button').filter((button) => button.querySelector('h3'));
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

  it('opens a searchable project knowledge graph with spec, work item and TC relations', () => {
    const enrichedItems = workItems.map((item) => {
      if (item.id === 'design-work') return {
        ...item, outputs: [{ name: '概要设计文档' }], suggested_assignee: '架构 Agent',
        acceptance_criteria: [{ criterion: '设计评审通过', expected_result: '无阻断问题' }],
      };
      if (item.id === 'requirements-work') return { ...item, suggested_assignee: 'owner-1' };
      if (item.id === 'design-gate') return { ...item, suggested_assignee: 'owner-4' };
      return item;
    });
    const newerDraft = { ...sourceSpec, id: 'prd-2', revision: 2, status: 'DRAFT', change_summary: '尚未拆分的新草稿' };
    const { rerender } = render(<TopLevelGraph projectTitle="支付平台" specs={[sourceSpec, newerDraft]} agentSpecs={[spec('requirements-work', 'requirements'), spec('design-work', 'design')]} workItems={enrichedItems} onOpenWorkItem={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '思维图谱' }));
    const graph = screen.getByRole('dialog', { name: '支付平台项目思维图谱' });
    expect(within(graph).getByText('Spec v1')).toBeTruthy();
    expect(within(graph).queryByText('FR-001')).toBeNull();
    fireEvent.click(within(graph).getByText('Spec v1'));
    expect(within(graph).queryByText('Spec v2')).toBeNull();
    expect(within(graph).getByText(/已拆分为 2 个 Agent Spec/)).toBeTruthy();
    expect(within(graph).getByText('FR-001')).toBeTruthy();
    expect(within(graph).queryByText('TC-001')).toBeNull();
    fireEvent.click(within(graph).getByText('FR-001'));
    expect(within(graph).getAllByText('TC-001').length).toBeGreaterThan(0);
    const search = within(graph).getByRole('textbox', { name: '搜索项目思维图谱' });
    fireEvent.change(search, { target: { value: '架构 Agent' } });
    expect(within(graph).queryByText('人物关联 · 1')).toBeNull();
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    expect(within(graph).getByText('人物关联 · 1')).toBeTruthy();
    expect(within(graph).getByText('系统设计')).toBeTruthy();
    expect(within(graph).getByText('概要设计文档')).toBeTruthy();
    fireEvent.click(within(graph).getByRole('button', { name: '复原思维图谱' }));
    expect((search as HTMLInputElement).value).toBe('');
    expect(within(graph).queryByText('系统设计')).toBeNull();
    fireEvent.change(search, { target: { value: '架构 Agent' } });
    fireEvent.click(within(graph).getByRole('button', { name: '确认搜索思维图谱' }));
    expect(within(graph).getByText('人物关联 · 1')).toBeTruthy();
    fireEvent.click(within(graph).getByRole('button', { name: '复原思维图谱' }));
    fireEvent.change(search, { target: { value: 'React architecture' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    expect(within(graph).getByText(/^SKILL/)).toBeTruthy();
    expect(within(graph).getByText('React architecture')).toBeTruthy();
    expect(within(graph).getByText('Agent 装配技能')).toBeTruthy();
    fireEvent.click(within(graph).getByRole('button', { name: '复原思维图谱' }));
    fireEvent.change(search, { target: { value: 'owner-3' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    expect(within(graph).getByText('精准匹配 · 0')).toBeTruthy();
    expect(within(graph).queryByText('owner-1')).toBeNull();
    expect(within(graph).queryByText('owner-4')).toBeNull();
    fireEvent.change(search, { target: { value: 'owner-1' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    expect(within(graph).getByText('精准匹配 · 1')).toBeTruthy();
    expect(within(graph).getByText('owner-1')).toBeTruthy();
    expect(within(graph).queryByText('owner-4')).toBeNull();
    fireEvent.click(within(graph).getByRole('button', { name: '复原思维图谱' }));
    fireEvent.change(search, { target: { value: 'fr-001' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    expect(within(graph).getByText('精准匹配 · 1')).toBeTruthy();
    expect(within(graph).getByText('FR-001')).toBeTruthy();
    expect(within(graph).queryByText('FR-002')).toBeNull();
    expect(within(graph).getByText('编写需求')).toBeTruthy();
    expect(within(graph).getByText('系统设计')).toBeTruthy();
    expect(within(graph).getAllByText('任务实现').length).toBeGreaterThan(0);
    expect(within(graph).getAllByText('验收验证').length).toBeGreaterThan(0);
    expect(within(graph).getAllByText('任务验收').length).toBeGreaterThan(0);
    fireEvent.click(within(graph).getByText('FR-001'));
    expect(within(graph).getByRole('region', { name: '节点追溯关系' })).toBeTruthy();
    expect(within(graph).getByText(/关联 WorkItem · 2/)).toBeTruthy();
    expect(within(graph).getByText(/关联 TC \/ AC/)).toBeTruthy();
    fireEvent.change(search, { target: { value: 'TC-001' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    expect(within(graph).getByText('精准匹配 · 4')).toBeTruthy();
    const viewport = graph.querySelector('.ff-project-mindmap-viewport') as HTMLDivElement;
    viewport.scrollLeft = 321;
    viewport.scrollTop = 147;
    rerender(<TopLevelGraph projectTitle="支付平台" specs={[{ ...sourceSpec }, { ...newerDraft }]} agentSpecs={[spec('requirements-work', 'requirements'), spec('design-work', 'design')]} workItems={[...enrichedItems]} onOpenWorkItem={vi.fn()} />);
    expect(viewport.scrollLeft).toBe(321);
    expect(viewport.scrollTop).toBe(147);
  });
});
