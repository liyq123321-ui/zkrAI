// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ApiWorkspace } from './ApiWorkspace';
import { PrdReviewPanel } from './PrdReviewPanel';
import type { SessionStateDto, SpecVersionDto } from './dto';

const state: SessionStateDto = {
  session_id:'session-1', project_id:'project-1', phase:'REVIEW', state_version:6,
  current_spec_version_id:'spec-1', current_spec_status:'REWORK', legal_actions:['approve','revise','restore_spec_version'],
  next_action:'REVISE_SPEC', outstanding_questions:[],
  review_findings:[{code:'SCOPE-001',message:'请确认数据库范围。',suggested_resolution:'明确是否需要数据库。',blocks_progress:true}],
};
const spec: SpecVersionDto = {
  id:'spec-1',project_id:'project-1',revision:1,content:{},markdown:'# 需求说明\n这是已保存的 PRD 正文。',
  generation_source:'agent',parent_version_id:null,change_summary:'初始版本',status:'REWORK',
  reviews:[{id:'review-1',kind:'SEMANTIC',verdict:'REWORK',findings:state.review_findings,comments:null,created_at:'2026-09-03T02:46:00Z',command_id:'command-1'}],
  generator_call_id:'generate-1',created_at:'2026-09-03T02:44:00Z',
};
const doc = {wi:'root-1',version:1,filename:'docs/prd/root-1/v1.md',pr_number:1,commit_sha:'abc123',content:spec.markdown,change_summary:'初始版本'};
let failLines = false;
let failComments = false;
let failDocument = false;
let failDiff = false;
let missingSavedSession = false;
let resourceOverrides: Record<string, unknown> = {};
let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  // jsdom has no native dialog implementation; exercise visibility here and native focus in browser QA.
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  failLines = false; failComments = false; failDocument = false; failDiff = false; missingSavedSession = false;
  resourceOverrides = {};
  localStorage.clear();
  fetchSpy = vi.fn(async (input: string | URL | Request) => {
    const path = new URL(String(input)).pathname;
    if (missingSavedSession && path.startsWith('/sessions/session-missing/')) {
      return new Response(JSON.stringify({detail:{code:'NOT_FOUND',message:'The requested workflow resource was not found.'}}), {status:404});
    }
    if ((failLines && path.endsWith('/commentable-lines')) || (failComments && path.endsWith('/comments')) || (failDocument && path === '/prd/root-1') || (failDiff && path.endsWith('/diff'))) {
      return new Response(JSON.stringify({detail:{code:'GITEA_UNAVAILABLE',message:'Gitea is temporarily unavailable.'}}), {status:503});
    }
    const payloads: Record<string, unknown> = {
      '/healthz':{status:'ok',database:'ok'},
      '/sessions/session-1/state':state,
      '/sessions/session-1/specs':[spec],
      '/sessions/session-1/work-items':[
        {id:'root-1',parent_id:null,kind:'ROOT',title:'知识问答',description:null,objective:'回答问题',status:'in_progress',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:null,responsible_role:'Owner',suggested_assignee:'owner-1',dependency_work_item_ids:[]},
        {id:'task-todo',parent_id:'root-1',kind:'TASK',title:'实现问答 API',description:null,objective:'提供问答接口',status:'todo',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:['FastAPI'],responsible_role:'Backend Engineer',suggested_assignee:'Backend Agent',dependency_work_item_ids:[]},
        {id:'task-running',parent_id:'root-1',kind:'TASK',title:'构建检索流程',description:null,objective:'接入检索能力',status:'in_progress',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:['Retrieval'],responsible_role:'AI Engineer',suggested_assignee:'AI Agent',dependency_work_item_ids:['task-todo']},
        {id:'task-done',parent_id:'root-1',kind:'TASK',title:'定义验收用例',description:null,objective:'建立测试基线',status:'completed',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:['Testing'],responsible_role:'QA Engineer',suggested_assignee:'QA Agent',dependency_work_item_ids:[]},
      ],
      '/sessions/session-1/agent-specs':[{id:'agent-spec-todo',work_item_id:'task-todo',source_spec_version_id:'spec-1',dependency_work_item_ids:[],created_at:'2026-09-03T02:47:00Z',content:{objective:'实现带权限控制的问答 API',scope:['实现 POST /questions'],exclusions:['不处理部署'],inputs:['已批准 PRD'],outputs:[{name:'问答 API',format:'JSON',required:true}],acceptance_criteria:[{requirement_ids:['FR-001'],criterion:'可以提交问题',verification_method:'API 测试',expected_result:'返回 200'}],required_skills:['FastAPI'],allowed_tools:['pytest'],allowed_paths:['backend/app/api'],test_obligations:['覆盖成功场景'],fixed_constraints:['保持接口契约'],configurable_parts:[],extension_points:[],risks:[],open_questions:[],responsible_role:'Backend Engineer',suggested_assignee:'Backend Agent'}}],
      '/sessions/session-1/events':[{id:'event-1',event_type:'AGENT_TRACE',actor_id:null,created_at:'2026-09-03T02:45:00',payload:{trace_id:'command-1',agent_call_id:'review-call-1',phase:'review_spec',status:'done',summary:'Reviewing specification quality',input_hash:'input-proof',output_hash:'output-proof',started_at:'2026-09-03T02:45:00',completed_at:'2026-09-03T02:46:00',safe_error_code:null}}],
      '/prd/root-1':doc,
      '/prd/root-1/diff':{wi:'root-1',version:1,filename:doc.filename,commit_sha:'abc123',patch:'@@ -1,2 +1,2 @@\n # 需求说明\n-旧要求\n+这是已保存的 PRD 正文。\n'},
      '/prd/root-1/versions':[doc],
      '/prd/root-1/comments':[],
      '/prd/root-1/commentable-lines':{wi:'root-1',version:1,filename:doc.filename,commit_sha:'abc123',lines:[{line:1,kind:'addition',text:'# 需求说明'},{line:2,kind:'addition',text:'这是已保存的 PRD 正文。'}]},
      ...resourceOverrides,
    };
    if (!(path in payloads)) throw new Error(`Unexpected test request: ${path}`);
    return new Response(JSON.stringify(payloads[path]), {status:200});
  });
  vi.stubGlobal('fetch',fetchSpy);
});
afterEach(() => {cleanup();localStorage.clear();vi.unstubAllGlobals();});

function renderPanel(onConfirmAndDecompose: (reviewNote: string) => Promise<void> = async () => undefined) {
  return render(<PrdReviewPanel wi="root-1" sessionState={state} fallbackSpec={spec} onConfirmAndDecompose={onConfirmAndDecompose} />);
}

describe('workspace regression', () => {
  it.each([
    {event_type:'AGENT_TRACE',payload:{phase:'plan_task',status:'done'},label:'细化任务实现方案'},
    {event_type:'AGENT_SPEC_DETAILS_ENRICHED',payload:{},label:'任务实现方案已补齐'},
  ])('shows a readable audit title for $label', async ({event_type,payload,label}) => {
    resourceOverrides = {
      '/sessions/session-1/events':[{id:'enrichment-event',event_type,payload,actor_id:null,created_at:'2026-09-03T02:45:00Z'}],
    };
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);
    const title = await screen.findByText(label,{selector:'summary'});
    expect(title.closest('summary')).toBeTruthy();
  });

  it.each(['planned', 'historical', 'missing-source'])('opens a real child card with %s task details bound to its original PRD', async (variant) => {
    const original = {...spec, id:'spec-original', revision:1, content:{
      functional_requirements:[{requirement_id:'FR-001',statement:'原批准要求：回答必须列出来源。',priority:'MUST'}],
      non_functional_requirements:[{requirement_id:'NFR-001',statement:'原批准要求：两秒内返回引用。',priority:'SHOULD'}],
    }};
    resourceOverrides = {
      '/sessions/session-1/specs':[
        {...spec, revision:2, content:{functional_requirements:[{requirement_id:'FR-001',statement:'新版要求：允许不列来源。',priority:'MUST'}]}},
        ...(variant === 'missing-source' ? [] : [original]),
      ],
      '/sessions/session-1/work-items':[{id:'child-1',parent_id:'root-1',kind:'TASK',title:'实现回答引用',description:null,objective:'提供可核查的回答',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:null,responsible_role:'前端工程师',suggested_assignee:null,dependency_work_item_ids:[]}],
      '/sessions/session-1/agent-specs':[{id:'agent-1',work_item_id:'child-1',source_spec_version_id:'spec-original',dependency_work_item_ids:[],created_at:spec.created_at,content:{
        objective:'提供可核查的回答',
        acceptance_criteria:[{requirement_ids:['FR-001','NFR-001'],criterion:'引用可打开',verification_method:'点击测试',expected_result:'显示原文'}],
        ...(variant === 'planned' ? {
          requirements:[{requirement_id:'FR-001',statement:'原批准要求：回答必须列出来源。',priority:'MUST'}],
          implementation_plan:{overview:'为回答挂接原文入口。',steps:[{step_id:'S1',title:'添加引用列表',requirement_ids:['FR-001'],implementation_method:'从回答引用字段读取文档编号并渲染链接。',expected_output:'可点击的回答引用列表。',verification_method:'点击引用并核对文档编号。'}]},
        } : {}),
      }}],
    };
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button',{name:/实现回答引用.*查看任务 Spec/}));
    const details = within(await screen.findByRole('dialog',{name:'任务详情'}));
    const requirements = within(details.getByRole('region',{name:'对应需求原文'}));
    expect(details.queryByText('新版要求：允许不列来源。')).toBeNull();
    if (variant === 'missing-source') {
      expect(requirements.getByText(/未找到绑定的来源 PRD/)).toBeTruthy();
      expect(details.queryByText('原批准要求：回答必须列出来源。')).toBeNull();
    } else {
      expect(requirements.getByText('原批准要求：回答必须列出来源。')).toBeTruthy();
    }
    if (variant === 'planned') {
      const plan = within(details.getByRole('region',{name:'实施方案'}));
      expect(plan.getByText('从回答引用字段读取文档编号并渲染链接。')).toBeTruthy();
      expect(plan.getByText('可点击的回答引用列表。')).toBeTruthy();
      expect(plan.getByText('点击引用并核对文档编号。')).toBeTruthy();
    } else {
      expect(details.getByText(/尚未生成实现方案/)).toBeTruthy();
      if (variant === 'historical') expect(requirements.getByText('原批准要求：两秒内返回引用。')).toBeTruthy();
    }
    fireEvent.click(details.getByRole('button',{name:'关闭详情'}));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it.each(['lines','comments','document','diff'])('keeps saved PRD readable and offers explicit fallback confirmation when %s cannot load', async (resource) => {
    failLines = resource === 'lines'; failComments = resource === 'comments'; failDocument = resource === 'document'; failDiff = resource === 'diff';
    const confirm = vi.fn(async () => undefined);
    renderPanel(confirm);
    fireEvent.click(screen.getByRole('button',{name:'正文'}));
    expect(await within(screen.getByRole('region',{name:'PRD 正文'})).findByText('这是已保存的 PRD 正文。')).toBeTruthy();
    await screen.findByRole('alert');
    const approve = screen.getByRole('button',{name:'确认 PRD 并开始任务拆解'});
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('button',{name:'发布批注并生成新版'})).toBeNull();
    fireEvent.change(screen.getByPlaceholderText('说明为什么接受当前审核发现'), {
      target: {value: '已阅读并接受当前审核发现'},
    });
    fireEvent.click(screen.getByRole('checkbox'));
    expect((approve as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(approve);
    await waitFor(() => expect(confirm).toHaveBeenCalledWith('已阅读并接受当前审核发现'));
    failLines = false; failComments = false; failDocument = false; failDiff = false;
    fireEvent.click(screen.getByRole('button',{name:'刷新 PRD'}));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(screen.getByRole('button',{name:'确认 PRD 并开始任务拆解'})).toBeTruthy();
  });

  it('opens the root PRD in a visible dialog and supports closing it', async () => {
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button',{name:/知识问答.*打开 PRD 审核/}));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button',{name:'Diff'}).getAttribute('aria-pressed')).toBe('true');
    expect(await within(within(dialog).getByRole('region',{name:'PRD Diff'})).findByText('+这是已保存的 PRD 正文。')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button',{name:'关闭详情'}));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('clears a saved Session that no longer exists after the local database is reset', async () => {
    missingSavedSession = true;
    localStorage.setItem('firstflight.active-session-id','session-missing');
    render(<ApiWorkspace />);

    await waitFor(() => expect(localStorage.getItem('firstflight.active-session-id')).toBeNull());
    expect(screen.getByText('新项目需求')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('places child WorkItems into three status columns using backend status', async () => {
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);

    const todo = await screen.findByRole('region',{name:'待开始 (To Do)'});
    const running = screen.getByRole('region',{name:'进行中 (In Progress)'});
    const done = screen.getByRole('region',{name:'已完成 (Done)'});
    expect(within(todo).getByRole('button',{name:/实现问答 API/})).toBeTruthy();
    expect(within(running).getByRole('button',{name:/构建检索流程/})).toBeTruthy();
    expect(within(done).getByRole('button',{name:/定义验收用例/})).toBeTruthy();

    fireEvent.click(within(todo).getByRole('button',{name:/实现问答 API/}));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading',{name:'工作范围'})).toBeTruthy();
    expect(within(dialog).getByText('实现 POST /questions')).toBeTruthy();
    expect(within(dialog).queryByText(/"objective"/)).toBeNull();
  });

  it('expands audit evidence and the review output associated with that event', async () => {
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);
    const summary = await screen.findByText(/审核 PRD 质量/, {selector:'summary'});
    fireEvent.click(summary);
    const details = summary.closest('details')!;
    expect(details.open).toBe(true);
    expect(within(details).getByText('output-proof')).toBeTruthy();
    expect(within(details).getByText('请确认数据库范围。')).toBeTruthy();
  });

  it('shows a practical next step without an unusable empty restore form', async () => {
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);
    const sidebar = screen.getByRole('complementary');
    await within(sidebar).findByText('需要修改');
    expect(within(sidebar).getByRole('button',{name:'查看 PRD 与审核意见'})).toBeTruthy();
    expect(within(sidebar).queryByRole('combobox')).toBeNull();
    expect(within(sidebar).queryByRole('button',{name:'基于历史版本创建新版'})).toBeNull();
  });
});

it('shows the complete rendered PRD alongside a diff that includes removed lines', async () => {
  renderPanel();
  fireEvent.click(screen.getByRole('button',{name:'并排查看'}));
  const body = await screen.findByRole('region',{name:'PRD 正文'});
  expect(within(body).getByRole('heading',{name:'需求说明'})).toBeTruthy();
  expect(within(body).getByText('这是已保存的 PRD 正文。')).toBeTruthy();
  const diff = await screen.findByRole('region',{name:'PRD Diff'});
  expect(await within(diff).findByText('-旧要求')).toBeTruthy();
  expect(within(diff).getByText('+这是已保存的 PRD 正文。')).toBeTruthy();
  expect(within(diff).queryByRole('button',{name:/旧要求/})).toBeNull();
});
