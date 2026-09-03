// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { AgentSpecDetail } from './AgentSpecDetail';
import type { AgentSpecDto, WorkItemDto } from './dto';

const item: WorkItemDto = {
  id: 'task-api',
  parent_id: 'milestone-1',
  kind: 'TASK',
  title: '实现 Session API',
  description: null,
  objective: '提供受工作流保护的 API',
  status: 'todo',
  scope: [],
  exclusions: [],
  outputs: null,
  acceptance_criteria: null,
  required_skills: ['FastAPI'],
  responsible_role: 'Backend Engineer',
  suggested_assignee: 'Backend Agent',
  dependency_work_item_ids: ['task-domain'],
};

const agentSpec: AgentSpecDto = {
  id: 'agent-spec-1',
  work_item_id: item.id,
  source_spec_version_id: 'spec-v1',
  dependency_work_item_ids: ['task-domain'],
  created_at: '2026-09-03T00:00:00Z',
  content: {
    objective: '实现 Session 的创建与查询接口',
    scope: ['实现 POST /sessions', '实现 GET /sessions/{id}'],
    exclusions: ['不实现部署'],
    inputs: ['已批准 PRD'],
    outputs: [{ name: 'Session API', format: 'JSON', required: true }],
    acceptance_criteria: [{ requirement_ids: ['FR-001'], criterion: '可创建 Session', verification_method: 'API 测试', expected_result: '返回 201' }],
    test_obligations: ['覆盖成功和冲突场景'],
    fixed_constraints: ['保持接口幂等'],
    required_skills: ['FastAPI', 'SQLAlchemy'],
    allowed_tools: ['pytest'],
    allowed_paths: ['backend/app/api'],
    responsible_role: 'Backend Engineer',
    suggested_assignee: 'Backend Agent',
    risks: ['并发写入冲突'],
  },
};

afterEach(cleanup);

describe('AgentSpecDetail', () => {
  it('presents an Agent Spec as readable sections instead of raw JSON', () => {
    render(<AgentSpecDetail item={item} agentSpecs={[agentSpec]} />);

    expect(screen.getAllByText('待开始')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: '工作范围' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '输入与交付物' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '验收标准与测试' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '实施边界与扩展点' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '风险与待确认事项' })).toBeTruthy();
    expect(screen.getByText('实现 POST /sessions')).toBeTruthy();
    expect(screen.getByText('返回 201')).toBeTruthy();
    expect(screen.queryByText(/"objective"/)).toBeNull();

    const agentCard = screen.getByRole('heading', { name: '责任 Agent' }).closest('section')!;
    expect(within(agentCard).getByText('Backend Agent')).toBeTruthy();
    expect(within(agentCard).getByText('Backend Engineer')).toBeTruthy();
  });
});
