// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { AgentSpecDetails } from './AgentSpecDetails';
import type { AgentSpecDto, SpecVersionDto } from './dto';

afterEach(cleanup);

const sourceSpec: SpecVersionDto = {
  id:'prd-original',project_id:'project-1',revision:1,status:'APPROVED',markdown:'',
  content:{
    functional_requirements:[
      {requirement_id:'FR-001',statement:'回答须包含可打开的来源。',priority:'MUST'},
      {requirement_id:'FR-002',statement:'不属于本任务的需求。',priority:'COULD'},
    ],
    non_functional_requirements:[{requirement_id:'NFR-001',statement:'引用须在两秒内可用。',priority:'SHOULD'}],
  },
  generation_source:'agent',parent_version_id:null,change_summary:'初稿',reviews:[],created_at:'2026-09-03T00:00:00Z',
};
const plan = {
  overview:'先校验引用数据，再渲染来源入口。',
  steps:[{
    step_id:'S1',title:'校验并展示引用',requirement_ids:['FR-001','NFR-001'],
    implementation_method:'在回答组件中校验文档 ID；缺失引用显示空态。',
    expected_output:'可打开原文的引用列表。',verification_method:'模拟空引用和有效引用，点击后核对文档 ID。',
  }],
};
function task(content: Record<string, unknown> = {}): AgentSpecDto {
  return {
    id:'agent-1',work_item_id:'task-1',source_spec_version_id:'prd-original',dependency_work_item_ids:[],created_at:sourceSpec.created_at,
    content:{
      objective:'让用户核查回答来源。',scope:['回答与引用区域'],exclusions:['全文搜索'],inputs:['回答引用数据'],
      outputs:[{name:'引用面板',format:'React component',required:true}],
      acceptance_criteria:[{requirement_ids:['FR-001','NFR-001'],criterion:'来源可追溯',verification_method:'点击来源',expected_result:'打开原文'}],
      fixed_constraints:['沿用现有 UI'],configurable_parts:[],extension_points:[],required_skills:['React'],allowed_tools:['vitest'],allowed_paths:['src/api'],
      responsible_role:'前端工程师',suggested_assignee:'frontend-agent',risks:['原文可能失效'],open_questions:[],
      ...content,
    },
  };
}

describe('AgentSpecDetails', () => {
  it('shows the saved requirements, scope, deliverables and actionable steps with links to original text', () => {
    render(<AgentSpecDetails agentSpec={task({
      requirements:[
        {requirement_id:'FR-001',statement:'保存的需求原文：回答须附来源。',priority:'MUST'},
        {requirement_id:'NFR-001',statement:'保存的需求原文：引用两秒内可用。',priority:'SHOULD'},
      ],implementation_plan:plan,
    })} sourceSpec={sourceSpec} />);
    expect(screen.getByText('让用户核查回答来源。')).toBeTruthy();
    expect(screen.getByText('回答与引用区域')).toBeTruthy();
    expect(screen.getByText('回答引用数据')).toBeTruthy();
    expect(screen.getByText('引用面板')).toBeTruthy();
    expect(screen.getByText('React component')).toBeTruthy();
    const requirements = within(screen.getByRole('region',{name:'对应需求原文'}));
    const original = requirements.getByText('保存的需求原文：回答须附来源。');
    expect(requirements.getByText('保存的需求原文：引用两秒内可用。')).toBeTruthy();
    expect(requirements.queryByText('回答须包含可打开的来源。')).toBeNull();
    const steps = within(screen.getByRole('region',{name:'实施方案'}));
    expect(steps.getByRole('heading',{name:/S1.*校验并展示引用/})).toBeTruthy();
    expect(steps.getByText('在回答组件中校验文档 ID；缺失引用显示空态。')).toBeTruthy();
    expect(steps.getByText('可打开原文的引用列表。')).toBeTruthy();
    expect(steps.getByText('模拟空引用和有效引用，点击后核对文档 ID。')).toBeTruthy();
    const target = steps.getByRole('link',{name:'FR-001'}).getAttribute('href')!;
    expect(document.getElementById(target.slice(1))?.contains(original)).toBe(true);
    const raw = screen.getByText('原始 JSON',{selector:'summary'}).closest('details')!;
    expect(raw.open).toBe(false);
    fireEvent.click(within(raw).getByText('原始 JSON'));
    expect(raw.open).toBe(true);
  });

  it('renders interface definitions with typed input/output fields, errors, data validation and decision status', () => {
    render(<AgentSpecDetails agentSpec={task({implementation_plan:{...plan,
      interface_notes:'沿用回答接口，新增引用数据。',
      interfaces:[{
        name:'获取引用',kind:'HTTP',definition:'GET /answers/{answer_id}/citations -> Citation[]',
        inputs:[{name:'answer_id',data_type:'string',required:true,description:'待查询回答编号'},{name:'cursor',data_type:'string | null',required:false,description:'下一页游标'}],
        outputs:[{name:'citations',data_type:'Citation[]',required:true,description:'按出现顺序返回引用'}],
        error_handling:['404：回答不存在时显示提示。'],
      }],
      data_structures:[{name:'Citation',definition:'{ document_id: string; title: string }',validation_rules:['document_id 必须非空。']}],
      design_decisions:[{decision:'复用回答编号',reason:'沿用批准的数据边界。',status:'FIXED'},{decision:'建议按引用顺序展示',reason:'便于逐条核查。',status:'PROPOSED'}],
    }})} />);
    const interfaces = within(screen.getByRole('region',{name:'接口定义'}));
    expect(interfaces.getByText('沿用回答接口，新增引用数据。')).toBeTruthy();
    expect(interfaces.getByText('GET /answers/{answer_id}/citations -> Citation[]')).toBeTruthy();
    const input = within(interfaces.getByRole('table',{name:'获取引用 · 输入字段'}));
    expect(input.getByRole('row',{name:'answer_id string 必填 待查询回答编号'})).toBeTruthy();
    expect(input.getByRole('row',{name:'cursor string | null 可选 下一页游标'})).toBeTruthy();
    const output = within(interfaces.getByRole('table',{name:'获取引用 · 输出字段'}));
    expect(output.getByRole('row',{name:'citations Citation[] 必填 按出现顺序返回引用'})).toBeTruthy();
    expect(interfaces.getByText('404：回答不存在时显示提示。')).toBeTruthy();
    expect(screen.getByText('{ document_id: string; title: string }')).toBeTruthy();
    expect(screen.getByText('document_id 必须非空。')).toBeTruthy();
    const decisions = within(screen.getByRole('region',{name:'设计决策'}));
    const proposed = within(decisions.getByText('建议按引用顺序展示').closest('li')!);
    expect(proposed.getByText('提案 · 待确认')).toBeTruthy();
    expect(proposed.getByText('便于逐条核查。')).toBeTruthy();
    expect(decisions.getByText('已确定')).toBeTruthy();
  });

  it.each([undefined, null])('recovers only the task FR/NFR from its exact bound PRD when the plan is %s', (implementation_plan) => {
    render(<AgentSpecDetails agentSpec={task({implementation_plan})} sourceSpec={sourceSpec} />);
    const requirements = within(screen.getByRole('region',{name:'对应需求原文'}));
    expect(requirements.getByText('回答须包含可打开的来源。')).toBeTruthy();
    expect(requirements.getByText('引用须在两秒内可用。')).toBeTruthy();
    expect(requirements.queryByText('不属于本任务的需求。')).toBeNull();
    expect(screen.getByText(/尚未生成实现方案/)).toBeTruthy();
    expect(screen.queryByRole('region',{name:'接口定义'})).toBeNull();
  });

  it('rejects a different PRD version even when its requirement IDs match', () => {
    render(<AgentSpecDetails agentSpec={task()} sourceSpec={{...sourceSpec,id:'prd-current'}} />);
    expect(screen.getByText(/未找到绑定的来源 PRD/)).toBeTruthy();
    expect(screen.queryByText('回答须包含可打开的来源。')).toBeNull();
  });

  it('does not invent interfaces when a documentation task explicitly has none', () => {
    render(<AgentSpecDetails agentSpec={task({implementation_plan:{...plan,
      interface_notes:'本任务仅交付使用说明，不涉及接口或数据结构。',interfaces:[],data_structures:[],design_decisions:[],
    }})} />);
    const interfaces = within(screen.getByRole('region',{name:'接口定义'}));
    expect(interfaces.getByText('本任务仅交付使用说明，不涉及接口或数据结构。')).toBeTruthy();
    expect(interfaces.queryByRole('table')).toBeNull();
    expect(screen.queryByText(/尚未生成实现方案/)).toBeNull();
  });

  it('keeps old plans readable when interface details were not stored', () => {
    render(<AgentSpecDetails agentSpec={task({implementation_plan:plan})} />);
    expect(screen.getByText('先校验引用数据，再渲染来源入口。')).toBeTruthy();
    expect(screen.getByText(/未记录接口说明与定义/)).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('handles malformed nested values without rendering objects as text or treating string false as required', () => {
    render(<AgentSpecDetails agentSpec={task({
      objective:{invalid:true},scope:[null,'有效范围'],inputs:null,outputs:[false,{name:'有效产出',required:'false'}],
      requirements:[null,3,{requirement_id:{},statement:[]}],acceptance_criteria:[null],
      implementation_plan:{steps:[null,{step_id:'S1',title:'残缺步骤',requirement_ids:[null,'FR-404'],implementation_method:{}}],
        interfaces:[null,{name:'残缺接口',inputs:[false,{name:'flag',data_type:'boolean',required:'false',description:'布尔字段'}],outputs:null,error_handling:[{}]}],
        data_structures:[null,{name:'残缺数据',definition:{}}],design_decisions:[null,{decision:'未知决策',status:'UNRECOGNIZED'}],
      },
    })} sourceSpec={sourceSpec} />);
    expect(screen.getByText('有效范围')).toBeTruthy();
    expect(screen.getByText('有效产出')).toBeTruthy();
    expect(screen.getByRole('row',{name:'flag boolean 未提供 布尔字段'})).toBeTruthy();
    expect(screen.getByText('FR-404（原文缺失）')).toBeTruthy();
    expect(screen.queryByRole('link',{name:'FR-404'})).toBeNull();
    expect(screen.getByText('状态未提供')).toBeTruthy();
    expect(screen.queryByText('[object Object]')).toBeNull();
  });
});
