// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ApiWorkspace } from './ApiWorkspace';
import { PrdReviewPanel } from './PrdReviewPanel';
import type { SessionStateDto, SessionSummaryDto, SpecVersionDto } from './dto';
import { useWorkspaceProjects } from './useWorkspaceProjects';

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
let resourceOverrideSequences: Record<string, unknown[]> = {};
let resourceFailures: Record<string, number> = {};
let resourceFailureSequences: Record<string, number[]> = {};
let resourceDelaySequences: Record<string, Promise<void>[]> = {};
let sessionCatalog: SessionSummaryDto[] = [];
let fetchSpy: ReturnType<typeof vi.fn>;

class WorkspaceEventSource {
  static instances: WorkspaceEventSource[] = [];
  close = vi.fn();
  onerror: ((event: Event) => void) | null = null;
  addEventListener = vi.fn();

  constructor(readonly url: string) {
    WorkspaceEventSource.instances.push(this);
  }
}

beforeEach(() => {
  // jsdom has no native dialog implementation; exercise visibility here and native focus in browser QA.
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  failLines = false; failComments = false; failDocument = false; failDiff = false; missingSavedSession = false;
  resourceOverrides = {};
  resourceOverrideSequences = {};
  resourceFailures = {};
  resourceFailureSequences = {};
  resourceDelaySequences = {};
  sessionCatalog = [{session_id:'session-1',project_id:'project-1',root_work_item_id:'root-1',title:'知识问答'}];
  localStorage.clear();
  WorkspaceEventSource.instances = [];
  vi.stubGlobal('EventSource', WorkspaceEventSource);
  fetchSpy = vi.fn(async (input: string | URL | Request, options?: RequestInit) => {
    const path = new URL(String(input)).pathname;
    const delay = resourceDelaySequences[path]?.shift();
    if (delay) await delay;
    const queuedFailure = resourceFailureSequences[path]?.shift();
    if (queuedFailure) {
      return new Response(JSON.stringify({detail:{code:'TEMPORARILY_UNAVAILABLE',message:'请稍后重试'}}),{status:queuedFailure});
    }
    if (resourceFailures[path]) {
      return new Response(JSON.stringify({detail:{code:'TEMPORARILY_UNAVAILABLE',message:'请稍后重试'}}),{status:resourceFailures[path]});
    }
    if (path === '/sessions' && options?.method !== 'POST') return new Response(JSON.stringify(sessionCatalog),{status:200});
    if (path === '/sessions' && options?.method === 'POST' && resourceOverrides[path]) {
      const created = resourceOverrides[path] as SessionStateDto;
      sessionCatalog.push({session_id:created.session_id,project_id:created.project_id,root_work_item_id:'root-2',title:'客户支持助手'});
    }
    if (missingSavedSession && path.startsWith('/sessions/session-missing/')) {
      return new Response(JSON.stringify({detail:{code:'NOT_FOUND',message:'The requested workflow resource was not found.'}}), {status:404});
    }
    if ((failLines && path.endsWith('/commentable-lines')) || (failComments && path.endsWith('/comments')) || (failDocument && path === '/prd/root-1') || (failDiff && path.endsWith('/diff'))) {
      return new Response(JSON.stringify({detail:{code:'GITEA_UNAVAILABLE',message:'Gitea is temporarily unavailable.'}}), {status:503});
    }
    const queuedPayload = resourceOverrideSequences[path]?.shift();
    if (queuedPayload !== undefined) {
      if (queuedPayload instanceof Response) return queuedPayload;
      return new Response(JSON.stringify(queuedPayload), {status:200});
    }
    const payloads: Record<string, unknown> = {
      '/healthz':{status:'ok',database:'ok'},
      '/sessions/session-1/state':state,
      '/sessions/session-1/specs':[spec],
      '/sessions/session-1/work-items':[
        {id:'root-1',parent_id:null,kind:'ROOT',title:'知识问答',summary:'知识问答助手',description:null,objective:'回答问题',status:'in_progress',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:null,responsible_role:'Owner',suggested_assignee:'owner-1',dependency_work_item_ids:[]},
        {id:'task-todo',parent_id:'root-1',kind:'TASK',title:'实现问答 API',description:null,objective:'提供问答接口',status:'todo',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:['FastAPI'],responsible_role:'Backend Engineer',suggested_assignee:'Backend Agent',dependency_work_item_ids:[]},
        {id:'task-running',parent_id:'root-1',kind:'TASK',title:'构建检索流程',description:null,objective:'接入检索能力',status:'in_progress',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:['Retrieval'],responsible_role:'AI Engineer',suggested_assignee:'AI Agent',dependency_work_item_ids:['task-todo']},
        {id:'task-done',parent_id:'root-1',kind:'TASK',title:'定义验收用例',description:null,objective:'建立测试基线',status:'completed',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:['Testing'],responsible_role:'QA Engineer',suggested_assignee:'QA Agent',dependency_work_item_ids:[]},
      ],
      '/sessions/session-1/agent-specs':[{id:'agent-spec-todo',work_item_id:'task-todo',source_spec_version_id:'spec-1',dependency_work_item_ids:[],created_at:'2026-09-03T02:47:00Z',content:{objective:'实现带权限控制的问答 API',scope:['实现 POST /questions'],exclusions:['不处理部署'],inputs:['已批准 PRD'],outputs:[{name:'问答 API',format:'JSON',required:true}],acceptance_criteria:[{requirement_ids:['FR-001'],criterion:'可以提交问题',verification_method:'API 测试',expected_result:'返回 200'}],required_skills:['FastAPI'],allowed_tools:['pytest'],allowed_paths:['backend/app/api'],test_obligations:['覆盖成功场景'],fixed_constraints:['保持接口契约'],configurable_parts:[],extension_points:[],risks:[],open_questions:[],responsible_role:'Backend Engineer',suggested_assignee:'Backend Agent'}}],
      '/sessions/session-1/events':[{id:'event-1',event_type:'AGENT_TRACE',actor_id:null,created_at:'2026-09-03T02:45:00',payload:{trace_id:'command-1',agent_call_id:'review-call-1',phase:'review_spec',status:'done',summary:'Reviewing specification quality',input_hash:'input-proof',output_hash:'output-proof',started_at:'2026-09-03T02:45:00',completed_at:'2026-09-03T02:46:00',safe_error_code:null}}],
      '/sessions/session-1/agents/runtime':[
        {agent_session_id:'agent-pm',project_id:'project-1',role:'PM Agent',provider:'codex',model:'gpt-test',purpose:'拆解需求',status:'running',current_operation:'decompose_spec',current_summary:'Decomposing the approved specification',current_call_id:'runtime-call-1',started_at:'2026-09-06T02:01:00Z',completed_at:null,call_count:2},
      ],
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
afterEach(() => {cleanup();localStorage.clear();delete window.GraphViewer;vi.unstubAllGlobals();vi.restoreAllMocks();vi.useRealTimers();});

function commandStatusRequestCount(): number {
  return fetchSpy.mock.calls.filter(([input, options]) =>
    (!options?.method || options.method === 'GET')
    && new URL(String(input)).pathname
      === '/sessions/session-1/commands/decompose-job-1'
  ).length;
}

function getRequestCount(path: string): number {
  return fetchSpy.mock.calls.filter(([input, options]) =>
    (!options?.method || options.method === 'GET')
    && new URL(String(input)).pathname === path
  ).length;
}

function commandPostBodies(): Array<Record<string, unknown>> {
  return fetchSpy.mock.calls
    .filter(([input, options]) =>
      options?.method === 'POST'
      && new URL(String(input)).pathname === '/sessions/session-1/commands')
    .map(([, options]) => JSON.parse(String(options?.body)) as Record<string, unknown>);
}

function deferred(): {
  promise: Promise<void>;
  resolve: () => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: () => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function renderPanel(
  onConfirmAndDecompose: (reviewNote: string) => Promise<void> = async () => undefined,
  sessionState: SessionStateDto = state,
) {
  return render(<PrdReviewPanel wi="root-1" sessionState={sessionState} fallbackSpec={spec} onConfirmAndDecompose={onConfirmAndDecompose} />);
}

function addSecondFlowProject() {
  sessionCatalog.push({session_id:'session-2',project_id:'project-2',root_work_item_id:'root-2',title:'客户支持助手'});
  resourceOverrides = {
    ...resourceOverrides,
    '/sessions/session-2/state':{...state,session_id:'session-2',project_id:'project-2',current_spec_version_id:null},
    '/sessions/session-2/specs':[],
    '/sessions/session-2/work-items':[
      {id:'root-2',kind:'ROOT',title:'客户支持助手',parent_id:null,dependency_work_item_ids:[]},
      {id:'milestone-2',kind:'MILESTONE',title:'支持流程里程碑',parent_id:'root-2',dependency_work_item_ids:[]},
      {id:'task-2',kind:'TASK',title:'处理客服工单',parent_id:'milestone-2',status:'todo',dependency_work_item_ids:[]},
    ],
    '/sessions/session-2/agent-specs':[],
    '/sessions/session-2/events':[],
  };
}

function QueuedTerminalReconciliationHarness() {
  const { state: currentState, updateSessionState } = useWorkspaceProjects();
  if (!currentState) return <span>loading</span>;
  return (
    <>
      <output aria-label="current queued Session state">
        {currentState.state_version}:{currentState.phase}
      </output>
      <button
        type="button"
        onClick={() => {
          updateSessionState({ ...currentState, phase: 'COMPLETE', state_version: 9 });
          updateSessionState({ ...currentState, phase: 'AGENT_SPECS_READY', state_version: 7 });
        }}
      >
        queue newer state then reconcile older terminal result
      </button>
    </>
  );
}

describe('workspace regression', () => {
  it('shows the generated HTML prototype beside document and Diff views', async () => {
    resourceOverrides['/prd/root-1'] = {
      ...doc,
      prototype: {
        status: 'ready',
        title: '知识问答交互原型',
        content_url: '/prd/root-1/v/1/prototype',
        generation_summary: '覆盖提问和回答流程。',
        error_code: null,
        error_message: null,
      },
    };
    renderPanel();

    fireEvent.click(await screen.findByRole('button', {name:'HTML 原型'}));

    const frame = screen.getByTitle('知识问答交互原型');
    expect(frame.getAttribute('src')).toBe('http://127.0.0.1:8088/prd/root-1/v/1/prototype');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts allow-forms');
    expect(screen.getByRole('link', {name:'在新窗口打开原型'}).getAttribute('href'))
      .toBe('http://127.0.0.1:8088/prd/root-1/v/1/prototype');
  });

  it('does not let an older terminal result overwrite a newer queued Session state', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    render(<QueuedTerminalReconciliationHarness />);
    await screen.findByText('6:REVIEW');

    fireEvent.click(screen.getByRole('button', {
      name: 'queue newer state then reconcile older terminal result',
    }));

    expect(screen.getByRole('status', { name: 'current queued Session state' }).textContent)
      .toBe('9:COMPLETE');
  });

  it('shows live Agent status above the audit records only while the audit tab is active', async () => {
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);
    expect(screen.queryByRole('region', {name:'Agent 实时运行状态'})).toBeNull();

    fireEvent.click(await screen.findByRole('button', {name:'打开审计记录'}));
    const panel = await screen.findByRole('region', {name:'Agent 实时运行状态'});
    const auditSummary = screen.getByText('安全审计摘要');
    expect(panel.compareDocumentPosition(auditSummary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(panel).getByText('PM Agent')).toBeTruthy();
    expect(fetchSpy.mock.calls.some(([input]) => String(input).endsWith('/sessions/session-1/agents/runtime'))).toBe(true);

    fireEvent.click(screen.getByRole('button', {name:/Kanban Board/}));
    expect(screen.queryByRole('region', {name:'Agent 实时运行状态'})).toBeNull();
  });

  it('keeps Kanban and task flow filters independent across tab switches', async () => {
    addSecondFlowProject();
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    render(<ApiWorkspace />);

    const kanbanSearch = await screen.findByRole('textbox', { name: '搜索工单' });
    fireEvent.change(kanbanSearch, { target: { value: '问答' } });
    fireEvent.change(screen.getByRole('combobox', { name: '搜索工单类型' }), { target: { value: 'ROOT' } });
    fireEvent.change(screen.getByRole('combobox', { name: '搜索工单执行者' }), { target: { value: 'owner-1' } });
    fireEvent.click(screen.getByRole('button', { name: '筛选主任务' }));
    let rootFilter = within(screen.getByRole('group', { name: '主任务筛选' }));
    fireEvent.click(rootFilter.getByRole('checkbox', { name: '客户支持助手（root-2）' }));
    fireEvent.keyDown(rootFilter.getByRole('checkbox', { name: '客户支持助手（root-2）' }), { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: /DAG Flow Map/ }));

    const flowSearch = screen.getByRole('textbox', { name: '搜索流转图工单' });
    expect((flowSearch as HTMLInputElement).value).toBe('');
    expect((screen.getByRole('combobox', { name: '搜索流转图工单类型' }) as HTMLSelectElement).value).toBe('ALL');
    expect((screen.getByRole('combobox', { name: '搜索流转图工单执行者' }) as HTMLSelectElement).value).toBe('ALL');
    expect(screen.getByRole('button', { name: '筛选主任务' }).textContent).toContain('全部任务');
    fireEvent.change(flowSearch, { target: { value: '检索' } });
    fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单类型' }), { target: { value: 'TASK' } });
    fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单执行者' }), { target: { value: 'AI Agent' } });
    fireEvent.click(screen.getByRole('button', { name: '筛选主任务' }));
    rootFilter = within(screen.getByRole('group', { name: '主任务筛选' }));
    fireEvent.click(rootFilter.getByRole('checkbox', { name: '知识问答助手（root-1）' }));
    fireEvent.keyDown(rootFilter.getByRole('checkbox', { name: '知识问答助手（root-1）' }), { key: 'Escape' });

    fireEvent.click(screen.getByRole('button', { name: /Kanban Board/ }));
    expect((screen.getByRole('textbox', { name: '搜索工单' }) as HTMLInputElement).value).toBe('问答');
    expect((screen.getByRole('combobox', { name: '搜索工单类型' }) as HTMLSelectElement).value).toBe('ROOT');
    expect((screen.getByRole('combobox', { name: '搜索工单执行者' }) as HTMLSelectElement).value).toBe('owner-1');
    fireEvent.click(screen.getByRole('button', { name: '筛选主任务' }));
    rootFilter = within(screen.getByRole('group', { name: '主任务筛选' }));
    expect((rootFilter.getByRole('checkbox', { name: '知识问答助手（root-1）' }) as HTMLInputElement).checked).toBe(true);
    expect((rootFilter.getByRole('checkbox', { name: '客户支持助手（root-2）' }) as HTMLInputElement).checked).toBe(false);
    fireEvent.keyDown(rootFilter.getByRole('checkbox', { name: '知识问答助手（root-1）' }), { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: /DAG Flow Map/ }));
    expect((screen.getByRole('textbox', { name: '搜索流转图工单' }) as HTMLInputElement).value).toBe('检索');
    expect((screen.getByRole('combobox', { name: '搜索流转图工单类型' }) as HTMLSelectElement).value).toBe('TASK');
    expect((screen.getByRole('combobox', { name: '搜索流转图工单执行者' }) as HTMLSelectElement).value).toBe('AI Agent');
    fireEvent.click(screen.getByRole('button', { name: '筛选主任务' }));
    rootFilter = within(screen.getByRole('group', { name: '主任务筛选' }));
    expect((rootFilter.getByRole('checkbox', { name: '知识问答助手（root-1）' }) as HTMLInputElement).checked).toBe(false);
    expect((rootFilter.getByRole('checkbox', { name: '客户支持助手（root-2）' }) as HTMLInputElement).checked).toBe(true);
  });

  it('normalizes invalid agents in both filter states after available agents change', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    render(<ApiWorkspace />);

    await screen.findByRole('button', { name: /实现问答 API/ });
    fireEvent.change(screen.getByRole('textbox', { name: '搜索工单' }), { target: { value: '问答' } });
    fireEvent.change(screen.getByRole('combobox', { name: '搜索工单执行者' }), { target: { value: 'Backend Agent' } });
    fireEvent.click(screen.getByRole('button', { name: /DAG Flow Map/ }));
    fireEvent.change(screen.getByRole('textbox', { name: '搜索流转图工单' }), { target: { value: '检索' } });
    fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单执行者' }), { target: { value: 'AI Agent' } });

    resourceOverrides['/sessions/session-1/work-items'] = [
      { id: 'root-1', parent_id: null, kind: 'ROOT', title: '知识问答', suggested_assignee: 'owner-1', dependency_work_item_ids: [] },
    ];
    fireEvent.click(screen.getByRole('button', { name: /Kanban Board/ }));
    fireEvent.click(screen.getByRole('button', { name: '刷新看板' }));

    await waitFor(() => expect((screen.getByRole('combobox', { name: '搜索工单执行者' }) as HTMLSelectElement).value).toBe('ALL'));
    expect((screen.getByRole('textbox', { name: '搜索工单' }) as HTMLInputElement).value).toBe('问答');
    fireEvent.click(screen.getByRole('button', { name: /DAG Flow Map/ }));
    expect((screen.getByRole('combobox', { name: '搜索流转图工单执行者' }) as HTMLSelectElement).value).toBe('ALL');
    expect((screen.getByRole('textbox', { name: '搜索流转图工单' }) as HTMLInputElement).value).toBe('检索');
  });

  it('shows separate PRD revision and decomposition controls and blocks decomposition on Agent findings', async () => {
    const enterDecomposition = vi.fn(async () => undefined);
    renderPanel(enterDecomposition);

    const revise = await screen.findByRole('button', {name: '确认批注并生成新版 PRD'});
    const decompose = screen.getByRole('button', {name: '确认当前 PRD，进入任务拆分'});
    const autoResolve = screen.getByRole('checkbox', {name: 'agent自动处理待审核项'});
    expect((revise as HTMLButtonElement).disabled).toBe(true);
    expect((decompose as HTMLButtonElement).disabled).toBe(true);
    expect((autoResolve as HTMLInputElement).disabled).toBe(false);
    expect(screen.getByText('Agent 自动审核仍有阻断项，请先通过批注生成修订版。')).toBeTruthy();

    fireEvent.click(decompose);
    expect(enterDecomposition).not.toHaveBeenCalled();
  });

  it('publishes Agent findings without comments only after explicit opt-in', async () => {
    const enterDecomposition = vi.fn(async () => undefined);
    resourceOverrides = {
      '/prd/root-1/reviews/publish':{task_id:'review-task-findings',base_version:1,comment_count:0},
      '/tasks/review-task-findings':{task_id:'review-task-findings',wi:'root-1',status:'processing',base_version:1,new_version:null,new_commit_sha:null,error:null},
    };
    renderPanel(enterDecomposition);

    const revise = await screen.findByRole('button', {name: '确认批注并生成新版 PRD'});
    const autoResolve = screen.getByRole('checkbox', {name: 'agent自动处理待审核项'});
    expect((revise as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(autoResolve);
    expect((revise as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(revise);

    await waitFor(() => {
      const request = fetchSpy.mock.calls.find(([input, options]) =>
        options?.method === 'POST' && String(input).endsWith('/prd/root-1/reviews/publish'));
      expect(request).toBeTruthy();
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({auto_resolve_findings:true});
    });
    expect(enterDecomposition).not.toHaveBeenCalled();
  });

  it('publishes comments as a new PRD review cycle without entering decomposition', async () => {
    const enterDecomposition = vi.fn(async () => undefined);
    const reviewed = {...state,current_spec_status:'HUMAN_REVIEW' as const,legal_actions:['approve','reject','rework','restore_spec_version'] as SessionStateDto['legal_actions'],review_findings:[]};
    resourceOverrides = {
      '/prd/root-1/comments':[{id:41,path:doc.filename,line:2,author_type:'human',body:'补充失败场景',resolved:false,replies:[]}],
      '/prd/root-1/reviews/publish':{task_id:'review-task-1',base_version:1,comment_count:1},
      '/tasks/review-task-1':{task_id:'review-task-1',wi:'root-1',status:'processing',base_version:1,new_version:null,new_commit_sha:null,error:null},
    };
    renderPanel(enterDecomposition, reviewed);

    const revise = await screen.findByRole('button', {name: '确认批注并生成新版 PRD'});
    const decompose = screen.getByRole('button', {name: '确认当前 PRD，进入任务拆分'});
    expect((screen.getByRole('checkbox', {name: 'agent自动处理待审核项'}) as HTMLInputElement).disabled).toBe(true);
    expect((revise as HTMLButtonElement).disabled).toBe(false);
    expect((decompose as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(revise);

    await waitFor(() => expect(fetchSpy.mock.calls.some(([input, options]) =>
      options?.method === 'POST' && String(input).endsWith('/prd/root-1/reviews/publish'))).toBe(true));
    expect(enterDecomposition).not.toHaveBeenCalled();
  });

  it('opens a dependency detail even when its card is filtered out and preserves the source draft', async () => {
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);
    await screen.findByRole('button',{name:/构建检索流程.*查看规划详情/});
    fireEvent.change(screen.getByRole('textbox',{name:'搜索工单'}),{target:{value:'构建检索流程'}});
    expect(screen.queryByRole('button',{name:/实现问答 API.*查看任务 Spec/})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:/构建检索流程.*查看规划详情/}));
    const sourceDialog = await screen.findByRole('dialog',{name:'任务详情'});
    fireEvent.change(within(sourceDialog).getByRole('textbox',{name:'给子 Agent 的指令'}),{target:{value:'保留检索任务草稿'}});
    sourceDialog.scrollTop = 400;
    fireEvent.click(within(sourceDialog).getByRole('button',{name:'打开依赖任务：实现问答 API'}));
    const targetDialog = screen.getByRole('dialog',{name:'任务详情'});
    expect(within(targetDialog).getByRole('heading',{name:'实现问答 API'})).toBeTruthy();
    expect(within(targetDialog).getByText('实现带权限控制的问答 API')).toBeTruthy();
    expect((within(targetDialog).getByRole('textbox',{name:'给子 Agent 的指令'}) as HTMLTextAreaElement).value).toBe('');
    expect(targetDialog.scrollTop).toBe(0);
    fireEvent.click(within(targetDialog).getByRole('button',{name:'关闭详情'}));
    expect((screen.getByRole('textbox',{name:'搜索工单'}) as HTMLInputElement).value).toBe('构建检索流程');
    fireEvent.click(screen.getByRole('button',{name:/构建检索流程.*查看规划详情/}));
    expect((screen.getByRole('textbox',{name:'给子 Agent 的指令'}) as HTMLTextAreaElement).value).toBe('保留检索任务草稿');
  });

  it('shows milestone child status and opens monitored task details', async () => {
    resourceOverrides['/sessions/session-1/work-items'] = [
      {id:'root-1',parent_id:null,kind:'ROOT',title:'知识问答',objective:'回答问题',status:'in_progress',suggested_assignee:'owner-1',dependency_work_item_ids:[]},
      {id:'milestone-1',parent_id:'root-1',kind:'MILESTONE',title:'检索能力交付',objective:'完成检索链路',status:'in_progress',suggested_assignee:'PM Agent',dependency_work_item_ids:[]},
      {id:'milestone-task-a',parent_id:'milestone-1',kind:'TASK',title:'实现向量检索',status:'completed',suggested_assignee:'AI Agent',dependency_work_item_ids:[]},
      {id:'milestone-task-b',parent_id:'milestone-1',kind:'TASK',title:'接入问答接口',status:'in_progress',suggested_assignee:'Backend Agent',dependency_work_item_ids:['milestone-task-a']},
      {id:'other-task',parent_id:'other-milestone',kind:'TASK',title:'其他里程碑任务',status:'blocked',dependency_work_item_ids:[]},
    ];
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);

    const milestoneCard = await screen.findByRole('button',{name:/检索能力交付.*查看规划详情/});
    expect(within(milestoneCard).getByText('2 个子任务 · 1 进行中')).toBeTruthy();

    fireEvent.click(milestoneCard);
    const milestoneDialog = await screen.findByRole('dialog',{name:'里程碑详情'});
    expect(within(milestoneDialog).getByRole('heading',{name:'子任务执行监控'})).toBeTruthy();
    const childButton = within(milestoneDialog).getByRole('button',{name:'打开子任务：接入问答接口，状态：进行中'});

    fireEvent.click(childButton);
    const taskDialog = await screen.findByRole('dialog',{name:'任务详情'});
    expect(within(taskDialog).getByRole('heading',{name:'接入问答接口'})).toBeTruthy();
    expect(within(taskDialog).queryByRole('heading',{name:'子任务执行监控'})).toBeNull();
  });

  it('loads all database projects without browser history and filters selected roots with their descendants', async () => {
    sessionCatalog.push({session_id:'session-2',project_id:'project-2',root_work_item_id:'root-2',title:'客户支持助手'});
    resourceOverrides = {
      '/sessions/session-2/state':{...state,session_id:'session-2',project_id:'project-2',current_spec_version_id:null},
      '/sessions/session-2/specs':[],
      '/sessions/session-2/work-items':[
        {id:'root-2',kind:'ROOT',title:'客户支持助手',parent_id:null,dependency_work_item_ids:[]},
        {id:'milestone-2',kind:'MILESTONE',title:'支持流程里程碑',parent_id:'root-2',dependency_work_item_ids:[]},
        {id:'task-2',kind:'TASK',title:'处理客服工单',parent_id:'milestone-2',status:'todo',dependency_work_item_ids:[]},
      ],
      '/sessions/session-2/agent-specs':[],
      '/sessions/session-2/events':[],
    };
    render(<ApiWorkspace />);
    await screen.findByRole('button',{name:/客户支持助手.*PRD 生成后可打开/});
    await screen.findByRole('button',{name:/实现问答 API.*查看任务 Spec/});
    fireEvent.click(screen.getByRole('button',{name:'筛选主任务'}));
    const filter = within(screen.getByRole('group',{name:'主任务筛选'}));
    expect(filter.getAllByRole('checkbox')).toHaveLength(2);
    fireEvent.click(filter.getByRole('checkbox',{name:'知识问答助手（root-1）'}));
    expect(screen.queryByRole('button',{name:/知识问答.*打开 PRD 审核/})).toBeNull();
    expect(screen.queryByRole('button',{name:/实现问答 API.*查看任务 Spec/})).toBeNull();
    expect(screen.getByRole('button',{name:/支持流程里程碑.*查看规划详情/})).toBeTruthy();
    expect(screen.getByRole('button',{name:/处理客服工单.*查看规划详情/})).toBeTruthy();
    fireEvent.click(filter.getByRole('checkbox',{name:'知识问答助手（root-1）'}));
    expect(screen.getByRole('button',{name:/实现问答 API.*查看任务 Spec/})).toBeTruthy();
    fireEvent.click(filter.getByRole('button',{name:'清空选择'}));
    expect(screen.queryByRole('button',{name:/处理客服工单.*查看规划详情/})).toBeNull();
    fireEvent.click(filter.getByRole('checkbox',{name:'客户支持助手（root-2）'}));
    fireEvent.keyDown(filter.getByRole('checkbox',{name:'客户支持助手（root-2）'}),{key:'Escape'});
    fireEvent.click(screen.getByRole('button',{name:'规划新主工单'}));
    expect(screen.getByRole('button',{name:/处理客服工单.*查看规划详情/})).toBeTruthy();
    expect(screen.queryByRole('button',{name:/实现问答 API.*查看任务 Spec/})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'筛选主任务'}));
    fireEvent.click(within(screen.getByRole('group',{name:'主任务筛选'})).getByRole('button',{name:'全部任务'}));
    expect(screen.getByRole('button',{name:/实现问答 API.*查看任务 Spec/})).toBeTruthy();
  });

  it('keeps the shared board when starting a new task and merges a newly created project across reloads', async () => {
    const secondState = {...state, session_id:'session-2', project_id:'project-2', current_spec_version_id:null, phase:'NEED_CLARIFICATION', legal_actions:['message']};
    resourceOverrides = {
      '/sessions':secondState,
      '/sessions/session-2/state':secondState,
      '/sessions/session-2/specs':[],
      '/sessions/session-2/work-items':[{id:'root-2',kind:'ROOT',title:'客户支持助手',objective:'处理客户问题',parent_id:null,dependency_work_item_ids:[]}],
      '/sessions/session-2/agent-specs':[],
      '/sessions/session-2/events':[],
    };
    localStorage.setItem('firstflight.active-session-id','session-1');
    const mounted = render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button',{name:/实现问答 API.*查看任务 Spec/}));
    fireEvent.change(screen.getByRole('textbox',{name:'给子 Agent 的指令'}),{target:{value:'只修改问答 API 的草稿'}});
    fireEvent.click(screen.getByRole('button',{name:'关闭详情'}));
    fireEvent.change(screen.getByRole('textbox',{name:'搜索工单'}),{target:{value:'实现问答'}});
    const requestCount = fetchSpy.mock.calls.length;
    fireEvent.click(screen.getByRole('button',{name:'创建新任务'}));
    expect(screen.getByText('新项目需求')).toBeTruthy();
    expect(screen.getByRole('button',{name:/实现问答 API.*查看任务 Spec/})).toBeTruthy();
    expect(fetchSpy.mock.calls.length).toBe(requestCount);
    expect((screen.getByRole('textbox',{name:'搜索工单'}) as HTMLInputElement).value).toBe('实现问答');
    fireEvent.change(screen.getByRole('textbox',{name:'搜索工单'}),{target:{value:''}});
    fireEvent.change(screen.getByRole('textbox',{name:'最终目标'}),{target:{value:'处理客户问题'}});
    fireEvent.click(screen.getByRole('button',{name:'创建并分析'}));
    await screen.findByRole('button',{name:/客户支持助手.*PRD 生成后可打开/});
    expect(screen.getByRole('button',{name:/实现问答 API.*查看任务 Spec/})).toBeTruthy();
    expect(screen.getByRole('button',{name:/知识问答.*打开 PRD 审核/})).toBeTruthy();

    fireEvent.click(screen.getByRole('button',{name:/实现问答 API.*查看任务 Spec/}));
    expect((screen.getByRole('textbox',{name:'给子 Agent 的指令'}) as HTMLTextAreaElement).value).toBe('只修改问答 API 的草稿');
    fireEvent.click(screen.getByRole('button',{name:'关闭详情'}));

    // The old project's PRD remains bound to that project while the second chat is active.
    fireEvent.click(screen.getByRole('button',{name:/知识问答.*打开 PRD 审核/}));
    const dialog = within(await screen.findByRole('dialog',{name:'PRD 审核'}));
    fireEvent.click(dialog.getByRole('button',{name:'正文'}));
    expect(await dialog.findByText('这是已保存的 PRD 正文。')).toBeTruthy();
    fireEvent.click(dialog.getByRole('button',{name:'关闭详情'}));
    expect(within(screen.getByRole('complementary')).getByText('客户支持助手',{selector:'.ff-message-bubble'})).toBeTruthy();

    mounted.unmount();
    render(<ApiWorkspace />);
    await screen.findByRole('button',{name:/客户支持助手.*PRD 生成后可打开/});
    await screen.findByRole('button',{name:/实现问答 API.*查看任务 Spec/});
    fireEvent.change(screen.getByRole('combobox',{name:'当前对话项目'}),{target:{value:'session-1'}});
    expect(within(screen.getByRole('complementary')).getByText('知识问答',{selector:'.ff-message-bubble'})).toBeTruthy();
    expect(screen.getByRole('button',{name:/客户支持助手.*PRD 生成后可打开/})).toBeTruthy();
  });

  it('restores healthy projects independently, removes missing sessions and retries temporary failures without changing the chat', async () => {
    missingSavedSession = true;
    localStorage.setItem('firstflight.active-session-id','session-missing');
    sessionCatalog.push(
      {session_id:'session-missing',project_id:'project-missing',root_work_item_id:null,title:'已删除项目'},
      {session_id:'session-offline',project_id:'project-offline',root_work_item_id:'root-offline',title:'恢复后的项目'},
    );
    resourceFailures['/sessions/session-offline/state'] = 503;
    render(<ApiWorkspace />);
    await screen.findByRole('button',{name:/实现问答 API.*查看任务 Spec/});
    expect(screen.getByText('新项目需求')).toBeTruthy();
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByRole('option',{name:'已删除项目'})).toBeNull();
    resourceFailures = {};
    resourceOverrides = {
      '/sessions/session-offline/state':{...state,session_id:'session-offline',project_id:'project-offline',current_spec_version_id:null},
      '/sessions/session-offline/specs':[],
      '/sessions/session-offline/work-items':[{id:'root-offline',kind:'ROOT',title:'恢复后的项目',parent_id:null,dependency_work_item_ids:[]}],
      '/sessions/session-offline/agent-specs':[],
      '/sessions/session-offline/events':[],
    };
    await waitFor(() => expect((screen.getByRole('button',{name:'刷新看板'}) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button',{name:'刷新看板'}));
    await screen.findByRole('button',{name:/恢复后的项目.*PRD 生成后可打开/});
    expect(screen.getByRole('button',{name:/实现问答 API.*查看任务 Spec/})).toBeTruthy();
    expect(screen.getByText('新项目需求')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('sends PRD approval to the selected card project even when a different project chat is active', async () => {
    localStorage.setItem('firstflight.active-session-id','session-2');
    sessionCatalog.push({session_id:'session-2',project_id:'project-2',root_work_item_id:'root-2',title:'另一个项目'});
    resourceOverrides = {
      '/sessions/session-1/state':{...state,current_spec_status:'HUMAN_REVIEW',legal_actions:['approve','reject','rework','restore_spec_version'],review_findings:[]},
      '/sessions/session-2/state':{...state,session_id:'session-2',project_id:'project-2',current_spec_version_id:null,legal_actions:[],phase:'NEED_CLARIFICATION'},
      '/sessions/session-2/specs':[],
      '/sessions/session-2/work-items':[{id:'root-2',kind:'ROOT',title:'另一个项目',parent_id:null,dependency_work_item_ids:[]}],
      '/sessions/session-2/agent-specs':[],
      '/sessions/session-2/events':[],
      '/sessions/session-1/commands':{state:{...state,phase:'APPROVED',current_spec_status:'APPROVED',state_version:7,legal_actions:[]}},
    };
    render(<ApiWorkspace />);
    await screen.findByRole('button',{name:/另一个项目.*PRD 生成后可打开/});
    fireEvent.click(await screen.findByRole('button',{name:/知识问答.*打开 PRD 审核/}));
    const dialog = within(await screen.findByRole('dialog',{name:'PRD 审核'}));
    const confirm = dialog.getByRole('button',{name:'确认当前 PRD，进入任务拆分'});
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(confirm);
    await screen.findByRole('dialog',{name:'任务详情'});
    const commands = fetchSpy.mock.calls.filter(([input, options]) => options?.method === 'POST' && String(input).endsWith('/commands'));
    expect(commands).toHaveLength(1);
    expect(String(commands[0][0])).toContain('/sessions/session-1/commands');
    expect(JSON.parse(commands[0][1].body)).toMatchObject({action:'approve',expected_state_version:6});
    fireEvent.click(screen.getByRole('button',{name:'关闭详情'}));
    expect((screen.getByRole('combobox',{name:'当前对话项目'}) as HTMLSelectElement).value).toBe('session-2');
    expect(within(screen.getByRole('complementary')).getByText('另一个项目',{selector:'.ff-message-bubble'})).toBeTruthy();
  });

  it('sends a work item execution command to its owning project instead of the active chat', async () => {
    localStorage.setItem('firstflight.active-session-id','session-2');
    sessionCatalog.push({session_id:'session-2',project_id:'project-2',root_work_item_id:'root-2',title:'另一个项目'});
    resourceOverrides = {
      '/sessions/session-1/work-items':[
        {id:'root-1',parent_id:null,kind:'ROOT',title:'知识问答',status:'in_progress',dependency_work_item_ids:[]},
        {id:'task-cross-project',parent_id:'root-1',kind:'TASK',title:'跨项目执行任务',status:'todo',suggested_assignee:'Backend Agent',dependency_work_item_ids:[],available_actions:['start_task']},
      ],
      '/sessions/session-2/state':{...state,session_id:'session-2',project_id:'project-2',current_spec_version_id:null,legal_actions:[],phase:'NEED_CLARIFICATION'},
      '/sessions/session-2/specs':[],
      '/sessions/session-2/work-items':[{id:'root-2',kind:'ROOT',title:'另一个项目',parent_id:null,dependency_work_item_ids:[]}],
      '/sessions/session-2/agent-specs':[],
      '/sessions/session-2/events':[],
      '/sessions/session-1/commands':{command_id:'command-start',state:{...state,state_version:7},created_resource_ids:[]},
    };
    render(<ApiWorkspace />);
    await screen.findByRole('button',{name:/跨项目执行任务/});
    fireEvent.click(screen.getByRole('button',{name:'开始'}));
    await waitFor(() => expect(fetchSpy.mock.calls.some(([input, options]) =>
      options?.method === 'POST' && String(input).endsWith('/sessions/session-1/commands'))).toBe(true));
    const command = fetchSpy.mock.calls.find(([input, options]) =>
      options?.method === 'POST' && String(input).endsWith('/sessions/session-1/commands'))!;
    expect(JSON.parse(command[1].body)).toMatchObject({
      action:'start_task',
      expected_state_version:6,
      payload:{work_item_id:'task-cross-project'},
    });
    expect((screen.getByRole('combobox',{name:'当前对话项目'}) as HTMLSelectElement).value).toBe('session-2');
  });

  it('keeps child-agent drafts separate between tasks without dispatching real commands', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', {name: /实现问答 API.*查看任务 Spec/}));
    let dialog = within(await screen.findByRole('dialog', {name: '任务详情'}));
    const requestCount = fetchSpy.mock.calls.length;
    const panel = within(dialog.getByRole('region', {name: '子 Agent 对话'}));
    const draft = panel.getByRole('textbox', {name: '给子 Agent 的指令'}) as HTMLTextAreaElement;
    fireEvent.click(panel.getByRole('button', {name: '修改需求'}));
    expect(draft.value).toContain('修改');
    fireEvent.change(draft, {target: {value: '请补充请求超时的验收标准。'}});
    fireEvent.click(panel.getByRole('button', {name: '修改需求'}));
    expect(draft.value).toBe('请补充请求超时的验收标准。');
    expect((panel.getByRole('button', {name: '发送指令'}) as HTMLButtonElement).disabled).toBe(true);
    expect((panel.getByRole('button', {name: '中断任务'}) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(panel.getByRole('button', {name: '发送指令'}));
    fireEvent.click(panel.getByRole('button', {name: '中断任务'}));
    expect(dialog.getAllByText('待开始')).toHaveLength(2);
    fireEvent.click(dialog.getByRole('button', {name: '关闭详情'}));

    fireEvent.click(screen.getByRole('button', {name: /构建检索流程/}));
    dialog = within(await screen.findByRole('dialog', {name: '任务详情'}));
    expect((dialog.getByRole('textbox', {name: '给子 Agent 的指令'}) as HTMLTextAreaElement).value).toBe('');
    fireEvent.change(dialog.getByRole('textbox', {name: '给子 Agent 的指令'}), {target: {value: '检索任务独立草稿'}});
    fireEvent.click(dialog.getByRole('button', {name: '关闭详情'}));

    fireEvent.click(screen.getByRole('button', {name: /实现问答 API.*查看任务 Spec/}));
    dialog = within(await screen.findByRole('dialog', {name: '任务详情'}));
    expect((dialog.getByRole('textbox', {name: '给子 Agent 的指令'}) as HTMLTextAreaElement).value).toBe('请补充请求超时的验收标准。');
    expect(fetchSpy.mock.calls.length).toBe(requestCount);
  });

  it('previews employee selection and unassignment across cards while leaving stored assignments unchanged', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    const mounted = render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', {name: /实现问答 API.*查看任务 Spec/}));
    let dialog = within(await screen.findByRole('dialog', {name: '任务详情'}));
    const requestCount = fetchSpy.mock.calls.length;
    let select = dialog.getByRole('combobox', {name: '指派员工'}) as HTMLSelectElement;
    expect(select.selectedOptions[0].textContent).toBe('Backend Agent');
    fireEvent.change(select, {target: {value: 'preview-frontend'}});
    expect(select.selectedOptions[0].textContent).toBe('前端同事（示例）');
    fireEvent.click(dialog.getByRole('button', {name: '关闭详情'}));
    expect(screen.getByRole('button', {name: /实现问答 API.*前端同事（示例）/})).toBeTruthy();
    expect(screen.getByRole('button', {name: /构建检索流程.*AI Agent/})).toBeTruthy();

    fireEvent.click(screen.getByRole('button', {name: /实现问答 API.*查看任务 Spec/}));
    dialog = within(await screen.findByRole('dialog', {name: '任务详情'}));
    select = dialog.getByRole('combobox', {name: '指派员工'}) as HTMLSelectElement;
    expect(select.value).toBe('preview-frontend');
    fireEvent.change(select, {target: {value: ''}});
    expect(select.selectedOptions[0].textContent).toBe('未指派');
    fireEvent.click(dialog.getByRole('button', {name: '关闭详情'}));
    expect(screen.getByRole('button', {name: /实现问答 API.*未指派/})).toBeTruthy();
    expect(fetchSpy.mock.calls.length).toBe(requestCount);

    mounted.unmount();
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', {name: /实现问答 API.*Backend Agent/}));
    dialog = within(await screen.findByRole('dialog', {name: '任务详情'}));
    expect((dialog.getByRole('combobox', {name: '指派员工'}) as HTMLSelectElement).selectedOptions[0].textContent).toBe('Backend Agent');
    expect((dialog.getByRole('textbox', {name: '给子 Agent 的指令'}) as HTMLTextAreaElement).value).toBe('');
  });

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
    const reviewed = {...state,current_spec_status:'HUMAN_REVIEW' as const,legal_actions:['approve','reject','rework','restore_spec_version'] as SessionStateDto['legal_actions'],review_findings:[]};
    renderPanel(confirm, reviewed);
    fireEvent.click(screen.getByRole('button',{name:'正文'}));
    expect(await within(screen.getByRole('region',{name:'PRD 正文'})).findByText('这是已保存的 PRD 正文。')).toBeTruthy();
    await screen.findByRole('alert');
    const approve = screen.getByRole('button',{name:'确认当前 PRD，进入任务拆分'});
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button',{name:'确认批注并生成新版 PRD'}) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', {
      name: '我已阅读当前 PRD 正文，确认在 Gitea Diff 与评论暂不可用时继续。此操作不会伪造或补写 Gitea 批注。',
    }));
    expect((approve as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(approve);
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(''));
    failLines = false; failComments = false; failDocument = false; failDiff = false;
    fireEvent.click(screen.getByRole('button',{name:'刷新 PRD'}));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(screen.getByRole('button',{name:'确认当前 PRD，进入任务拆分'})).toBeTruthy();
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

  it('uses a root summary on the board and in the root-task filter without renaming child work items', async () => {
    resourceOverrides['/sessions/session-1/work-items'] = [
      {id:'root-1',parent_id:null,kind:'ROOT',title:'交付一个仅在本机运行的温度换算器',summary:'本地温度换算器',objective:'输入摄氏温度并换算华氏温度',status:'in_progress',dependency_work_item_ids:[]},
      {id:'milestone-1',parent_id:'root-1',kind:'MILESTONE',title:'完成界面交付',objective:'交付换算界面',status:'in_progress',dependency_work_item_ids:[]},
      {id:'task-1',parent_id:'milestone-1',kind:'TASK',title:'实现换算按钮',objective:'完成公式计算',status:'todo',dependency_work_item_ids:[]},
    ];
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);

    const rootLane = await screen.findByRole('region', { name: '项目需求 (Root)' });
    const rootCard = within(rootLane).getByRole('button', { name: /本地温度换算器.*打开 PRD 审核/ });
    expect(within(rootCard).getByRole('heading', { name: '本地温度换算器' })).toBeTruthy();
    expect(within(rootCard).getByText(/交付一个仅在本机运行的温度换算器/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /完成界面交付.*查看规划详情/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /实现换算按钮.*查看规划详情/ })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '筛选主任务' }));
    expect(within(screen.getByRole('group', { name: '主任务筛选' }))
      .getByRole('checkbox', { name: '本地温度换算器（root-1）' })).toBeTruthy();
  });

  it('falls back from an invalid runtime summary in both ROOT card and root-task filter labels', async () => {
    resourceOverrides['/sessions/session-1/work-items'] = [
      {id:'root-1',parent_id:null,kind:'ROOT',title:'交付一个仅在本机运行的温度换算器',summary:'未修剪的摘要 ',objective:'输入摄氏温度并换算华氏温度',status:'in_progress',dependency_work_item_ids:[]},
    ];
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);

    const rootLane = await screen.findByRole('region', { name: '项目需求 (Root)' });
    expect(within(rootLane).getByRole('heading', { name: '交付一个仅在本机运行的温度换算器' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '筛选主任务' }));
    expect(within(screen.getByRole('group', { name: '主任务筛选' }))
      .getByRole('checkbox', { name: '交付一个仅在本机运行的温度换算器（root-1）' })).toBeTruthy();
  });

  it('keeps UUID copying available when the adjacent PRD card action is disabled', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    resourceOverrides['/sessions/session-1/state'] = { ...state, current_spec_version_id: null };
    resourceOverrides['/sessions/session-1/specs'] = [];
    resourceOverrides['/sessions/session-1/work-items'] = [
      {id:'root-1',parent_id:null,kind:'ROOT',title:'尚未生成 PRD 的项目',summary:'待生成 PRD 项目',objective:'等待生成 PRD',status:'in_progress',dependency_work_item_ids:[]},
    ];
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);

    const copyId = await screen.findByRole('button', { name: '复制工单 UUID：root-1' });
    expect((copyId as HTMLButtonElement).disabled).toBe(false);
    expect(copyId.parentElement?.closest('button')).toBeNull();
    expect(copyId.parentElement?.closest('[role="button"]')).toBeNull();
    fireEvent.click(copyId);

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('root-1'));
    expect(screen.getByRole('status').textContent).toBe('已复制 UUID：root-1');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders task dependencies as project swimlanes in the flow map', async () => {
    localStorage.setItem('firstflight.active-session-id','session-1');
    render(<ApiWorkspace />);

    fireEvent.click(await screen.findByRole('button',{name:/DAG Flow Map/}));
    const graph = await screen.findByRole('region',{name:'知识问答任务依赖图'});
    expect(within(graph).getByRole('button',{name:/实现问答 API/})).toBeTruthy();
    expect(within(graph).getByRole('button',{name:/构建检索流程/})).toBeTruthy();

    fireEvent.click(within(graph).getByRole('button',{name:/构建检索流程/}));
    const dialog = await screen.findByRole('dialog',{name:'任务详情'});
    expect(within(dialog).getByRole('heading',{name:'构建检索流程'})).toBeTruthy();
  });

  it('filters flow nodes by root, kind, agent, and search and shows an empty result', async () => {
    addSecondFlowProject();
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /DAG Flow Map/ }));

    fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单类型' }), { target: { value: 'TASK' } });
    fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单执行者' }), { target: { value: 'AI Agent' } });
    fireEvent.change(screen.getByRole('textbox', { name: '搜索流转图工单' }), { target: { value: '检索' } });

    expect(screen.getByRole('button', { name: /构建检索流程/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /实现问答 API/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /知识问答.*Owner/ })).toBeNull();
    expect(screen.getByText('1 NODES')).toBeTruthy();

    fireEvent.change(screen.getByRole('textbox', { name: '搜索流转图工单' }), { target: { value: '不存在的工单' } });
    expect(screen.getByRole('status').textContent).toBe('没有符合当前筛选条件的任务');
  });

  it('distinguishes backend-empty lanes from lanes hidden by flow filters', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /DAG Flow Map/ }));

    const rootLane = screen.getByText('项目需求 (Root)', { selector: '.ff-flow-lane > header' }).closest<HTMLElement>('.ff-flow-lane')!;
    const milestoneLane = screen.getByText('里程碑 (Milestones)', { selector: '.ff-flow-lane > header' }).closest<HTMLElement>('.ff-flow-lane')!;
    expect(within(milestoneLane).getByText('等待后端生成')).toBeTruthy();

    fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单类型' }), { target: { value: 'TASK' } });
    expect(within(rootLane).getByText('当前筛选条件已隐藏此类节点')).toBeTruthy();
    expect(within(milestoneLane).getByText('等待后端生成')).toBeTruthy();

    fireEvent.change(screen.getByRole('combobox', { name: '搜索流转图工单类型' }), { target: { value: 'ROOT' } });
    expect(within(screen.getByRole('region', { name: '子任务 (Tasks)' })).getByText('当前筛选条件已隐藏此类节点')).toBeTruthy();
  });

  it('filters the flow map to selected roots', async () => {
    addSecondFlowProject();
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /DAG Flow Map/ }));
    await screen.findByText('客户支持助手', { selector: '.ff-flow-node strong' });

    fireEvent.click(screen.getByRole('button', { name: '筛选主任务' }));
    const filter = within(screen.getByRole('group', { name: '主任务筛选' }));
    fireEvent.click(filter.getByRole('checkbox', { name: '知识问答助手（root-1）' }));

    expect(screen.queryByRole('button', { name: /知识问答.*Owner/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /实现问答 API/ })).toBeNull();
    expect(screen.queryByRole('region', { name: '知识问答任务依赖图' })).toBeNull();
    expect(screen.getByRole('button', { name: /客户支持助手/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /支持流程里程碑/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /处理客服工单/ })).toBeTruthy();
    expect(screen.getByRole('region', { name: '客户支持助手任务依赖图' })).toBeTruthy();
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
    expect(within(sidebar).queryByRole('combobox',{name:'历史 PRD 版本'})).toBeNull();
    expect(within(sidebar).queryByRole('button',{name:'基于历史版本创建新版'})).toBeNull();
  });

  it('submits decomposition once and polls its job every five seconds', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    const approved = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    resourceOverrides['/sessions/session-1/state'] = approved;
    resourceOverrides['/sessions/session-1/commands'] = {
      command_id: 'decompose-job-1',
      status: 'pending',
      status_url: '/sessions/session-1/commands/decompose-job-1',
      events_url: '/sessions/session-1/commands/decompose-job-1/events',
    };
    resourceOverrides['/sessions/session-1/commands/decompose-job-1'] = {
      command_id: 'decompose-job-1', status: 'succeeded', status_version: 3,
      result: { command_id: 'decompose-job-1', state: { ...approved, phase: 'AGENT_SPECS_READY', state_version: 7 }, created_resource_ids: ['task-created'] },
      error: null, created_at: '2026-09-05T00:00:00Z', started_at: '2026-09-05T00:00:01Z', completed_at: '2026-09-05T00:00:10Z',
    };

    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    const decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));
    vi.useFakeTimers();
    fireEvent.click(decompose);

    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSpy.mock.calls.some(([input, options]) =>
      options?.method === 'POST' && String(input).endsWith('/sessions/session-1/commands')
    )).toBe(true);
    expect(screen.getByText(/正在后台拆解子 WorkItem/)).toBeTruthy();
    await vi.advanceTimersByTimeAsync(4_999);
    expect(commandStatusRequestCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(commandStatusRequestCount()).toBe(1);
    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBeNull();
    expect(fetchSpy.mock.calls.filter(([input]) =>
      new URL(String(input)).pathname === '/sessions/session-1/work-items'
    )).toHaveLength(2);
  });

  it('resumes a saved decomposition job after reload without posting it again', async () => {
    vi.useFakeTimers();
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    localStorage.setItem('firstflight.decomposition-job.session-1', 'saved-job');
    resourceOverrides['/sessions/session-1/commands/saved-job'] = {
      command_id: 'saved-job', status: 'processing', status_version: 2,
      result: null, error: null, created_at: '2026-09-05T00:00:00Z', started_at: '2026-09-05T00:00:01Z', completed_at: null,
    };
    render(<ApiWorkspace />);
    await vi.advanceTimersByTimeAsync(1);
    expect(WorkspaceEventSource.instances).toHaveLength(1);
    expect(getRequestCount('/sessions/session-1/commands/saved-job')).toBe(1);
    await vi.advanceTimersByTimeAsync(4_998);
    expect(getRequestCount('/sessions/session-1/commands/saved-job')).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(getRequestCount('/sessions/session-1/commands/saved-job')).toBe(2);
    expect(fetchSpy.mock.calls.some(([input, options]) =>
      options?.method === 'POST' && String(input).endsWith('/commands')
    )).toBe(false);
  });

  it('renders the durable decomposition stage reported by the backend', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    localStorage.setItem('firstflight.decomposition-job.session-1', 'saved-job');
    resourceOverrides['/sessions/session-1/commands/saved-job'] = {
      command_id: 'saved-job',
      status: 'processing',
      status_version: 7,
      result: null,
      error: null,
      progress_stage: 'task_planning',
      progress_message: '任务边界已生成，正在规划 6 个子任务。',
      last_activity_at: '2026-09-05T00:00:05Z',
      created_at: '2026-09-05T00:00:00Z',
      started_at: '2026-09-05T00:00:01Z',
      completed_at: null,
    };

    render(<ApiWorkspace />);

    expect(await screen.findByText('任务边界已生成，正在规划 6 个子任务。')).toBeTruthy();
  });

  it('recovers a stale decomposition after a concurrent client advances state and retries with a new command ID', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    const firstState = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    const concurrentState = { ...firstState, state_version: firstState.state_version + 1 };
    const firstCommandId = '00000000-0000-4000-8000-000000000001';
    const retryCommandId = '00000000-0000-4000-8000-000000000002';
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce(firstCommandId)
      .mockReturnValueOnce(retryCommandId);
    resourceOverrideSequences['/sessions/session-1/state'] = [firstState];
    resourceOverrides['/sessions/session-1/state'] = concurrentState;
    resourceOverrideSequences['/sessions/session-1/commands'] = [
      {
        command_id: firstCommandId,
        status: 'pending',
        status_url: `/sessions/session-1/commands/${firstCommandId}`,
        events_url: `/sessions/session-1/commands/${firstCommandId}/events`,
      },
      {
        command_id: retryCommandId,
        status: 'pending',
        status_url: `/sessions/session-1/commands/${retryCommandId}`,
        events_url: `/sessions/session-1/commands/${retryCommandId}/events`,
      },
    ];
    resourceOverrides[`/sessions/session-1/commands/${firstCommandId}`] = {
      command_id: firstCommandId,
      status: 'failed',
      status_version: 3,
      result: null,
      error: {
        code: 'STALE_STATE',
        message: 'backend-internal stale comparison detail',
      },
      created_at: '2026-09-05T00:00:00Z',
      started_at: '2026-09-05T00:00:01Z',
      completed_at: '2026-09-05T00:00:10Z',
    };
    resourceOverrides[`/sessions/session-1/commands/${retryCommandId}`] = {
      command_id: retryCommandId,
      status: 'processing',
      status_version: 2,
      result: null,
      error: null,
      created_at: '2026-09-05T00:00:11Z',
      started_at: '2026-09-05T00:00:12Z',
      completed_at: null,
    };

    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    let decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));
    vi.useFakeTimers();

    fireEvent.click(decompose);
    await vi.advanceTimersByTimeAsync(5_000);
    await vi.advanceTimersByTimeAsync(0);

    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBeNull();
    expect(screen.getAllByText(/状态已被其他操作更新。页面已刷新/)).not.toHaveLength(0);
    expect(screen.queryByText(/backend-internal stale comparison detail/)).toBeNull();
    expect(WorkspaceEventSource.instances[0].close).toHaveBeenCalledTimes(1);
    expect(getRequestCount('/sessions/session-1/state')).toBe(2);

    decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    expect((decompose as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(decompose);
    await vi.advanceTimersByTimeAsync(0);

    expect(commandPostBodies()).toMatchObject([
      { command_id: firstCommandId, expected_state_version: firstState.state_version },
      { command_id: retryCommandId, expected_state_version: concurrentState.state_version },
    ]);
    expect(WorkspaceEventSource.instances).toHaveLength(2);
    expect(WorkspaceEventSource.instances[1].url).toContain(retryCommandId);
  });

  it('refreshes after synchronous approval becomes stale before decomposition is accepted', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    const reviewState = {
      ...state,
      current_spec_status: 'HUMAN_REVIEW' as const,
      legal_actions: ['approve'] as SessionStateDto['legal_actions'],
      review_findings: [],
    };
    const concurrentState = {
      ...reviewState,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
      state_version: reviewState.state_version + 1,
    };
    const approveCommandId = '00000000-0000-4000-8000-000000000011';
    const decomposeCommandId = '00000000-0000-4000-8000-000000000012';
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce(approveCommandId)
      .mockReturnValueOnce(decomposeCommandId);
    resourceOverrideSequences['/sessions/session-1/state'] = [reviewState];
    resourceOverrides['/sessions/session-1/state'] = concurrentState;
    resourceOverrideSequences['/sessions/session-1/commands'] = [
      new Response(JSON.stringify({
        detail: {
          code: 'STALE_STATE',
          message: 'internal approve expected=6 actual=7',
          errors: [],
        },
      }), { status: 409 }),
      {
        command_id: decomposeCommandId,
        status: 'pending',
        status_url: `/sessions/session-1/commands/${decomposeCommandId}`,
        events_url: `/sessions/session-1/commands/${decomposeCommandId}/events`,
      },
    ];

    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    const approve = dialog.getByRole('button', { name: '确认当前 PRD，进入任务拆分' });
    await waitFor(() => expect((approve as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(approve);

    await waitFor(() => expect(getRequestCount('/sessions/session-1/state')).toBe(2));
    expect(commandPostBodies()).toEqual([{
      command_id: approveCommandId,
      action: 'approve',
      expected_state_version: reviewState.state_version,
      payload: {},
    }]);
    expect(screen.getAllByText('状态已被其他操作更新。页面已刷新，请确认最新状态后重新提交。')).not.toHaveLength(0);
    expect(screen.queryByText(/internal approve expected=6 actual=7/)).toBeNull();
    expect(WorkspaceEventSource.instances).toHaveLength(0);

    const decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    expect((decompose as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(decompose);

    await waitFor(() => expect(commandPostBodies()).toHaveLength(2));
    expect(commandPostBodies()[1]).toEqual({
      command_id: decomposeCommandId,
      action: 'convert_to_work_item',
      expected_state_version: concurrentState.state_version,
      payload: {},
    });
    expect(WorkspaceEventSource.instances).toHaveLength(1);
    expect(WorkspaceEventSource.instances[0].url).toContain(decomposeCommandId);
  });

  it('refreshes and rotates identity when decomposition POST is stale before acceptance', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    const approvedState = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    const concurrentState = {
      ...approvedState,
      state_version: approvedState.state_version + 1,
    };
    const staleCommandId = '00000000-0000-4000-8000-000000000021';
    const retryCommandId = '00000000-0000-4000-8000-000000000022';
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce(staleCommandId)
      .mockReturnValueOnce(retryCommandId);
    resourceOverrideSequences['/sessions/session-1/state'] = [approvedState];
    resourceOverrides['/sessions/session-1/state'] = concurrentState;
    resourceOverrideSequences['/sessions/session-1/commands'] = [
      new Response(JSON.stringify({
        detail: {
          code: 'STALE_STATE',
          message: 'internal decomposition expected=6 actual=7',
          errors: [],
        },
      }), { status: 409 }),
      {
        command_id: retryCommandId,
        status: 'pending',
        status_url: `/sessions/session-1/commands/${retryCommandId}`,
        events_url: `/sessions/session-1/commands/${retryCommandId}/events`,
      },
    ];

    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    let decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(decompose);

    await waitFor(() => expect(getRequestCount('/sessions/session-1/state')).toBe(2));
    expect(commandPostBodies()).toEqual([{
      command_id: staleCommandId,
      action: 'convert_to_work_item',
      expected_state_version: approvedState.state_version,
      payload: {},
    }]);
    expect(screen.getAllByText('状态已被其他操作更新。页面已刷新，请确认最新状态后重新提交。')).not.toHaveLength(0);
    expect(screen.queryByText(/internal decomposition expected=6 actual=7/)).toBeNull();
    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBeNull();
    expect(WorkspaceEventSource.instances).toHaveLength(0);

    decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    expect((decompose as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(decompose);

    await waitFor(() => expect(commandPostBodies()).toHaveLength(2));
    expect(commandPostBodies()[1]).toEqual({
      command_id: retryCommandId,
      action: 'convert_to_work_item',
      expected_state_version: concurrentState.state_version,
      payload: {},
    });
    expect(WorkspaceEventSource.instances).toHaveLength(1);
    expect(WorkspaceEventSource.instances[0].url).toContain(retryCommandId);
  });

  it('keeps a succeeded job recoverable until its failed resource refresh later succeeds', async () => {
    vi.useFakeTimers();
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    localStorage.setItem('firstflight.decomposition-job.session-1', 'saved-job');
    sessionCatalog.push({
      session_id: 'session-2', project_id: 'project-2',
      root_work_item_id: 'root-2', title: '客户支持助手',
    });
    const terminalState = {
      ...state, phase: 'AGENT_SPECS_READY', state_version: 7,
    };
    resourceOverrides = {
      '/sessions/session-1/commands/saved-job': {
        command_id: 'saved-job', status: 'succeeded', status_version: 3,
        result: {
          command_id: 'saved-job', state: terminalState,
          created_resource_ids: ['task-created'],
        },
        error: null, created_at: '2026-09-05T00:00:00Z',
        started_at: '2026-09-05T00:00:01Z', completed_at: '2026-09-05T00:00:10Z',
      },
      '/sessions/session-2/state': {
        ...state, session_id: 'session-2', project_id: 'project-2',
        current_spec_version_id: null, legal_actions: [], phase: 'NEED_CLARIFICATION',
      },
      '/sessions/session-2/specs': [],
      '/sessions/session-2/work-items': [
        { id: 'root-2', kind: 'ROOT', title: '客户支持助手', parent_id: null, dependency_work_item_ids: [] },
      ],
      '/sessions/session-2/agent-specs': [],
      '/sessions/session-2/events': [],
    };
    resourceFailureSequences['/sessions/session-1/state'] = [0, 503];

    render(<ApiWorkspace />);
    await vi.advanceTimersByTimeAsync(1);
    expect(WorkspaceEventSource.instances).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(4_999);
    await vi.advanceTimersByTimeAsync(0);
    expect(getRequestCount('/sessions/session-1/commands/saved-job')).toBe(1);
    expect(getRequestCount('/sessions/session-1/state')).toBe(2);
    expect(getRequestCount('/sessions/session-1/work-items')).toBe(1);
    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBe('saved-job');
    expect(screen.getAllByText(/TEMPORARILY_UNAVAILABLE：请稍后重试/)).not.toHaveLength(0);

    const projectPicker = screen.getByRole('combobox', { name: '当前对话项目' });
    fireEvent.change(projectPicker, { target: { value: 'session-2' } });
    fireEvent.change(projectPicker, { target: { value: 'session-1' } });
    await vi.advanceTimersByTimeAsync(0);
    expect(WorkspaceEventSource.instances).toHaveLength(2);

    await vi.advanceTimersByTimeAsync(5_000);
    await vi.advanceTimersByTimeAsync(0);
    expect(getRequestCount('/sessions/session-1/commands/saved-job')).toBe(2);
    expect(getRequestCount('/sessions/session-1/state')).toBe(3);
    expect(getRequestCount('/sessions/session-1/work-items')).toBe(2);
    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBeNull();
  });

  it('shows the durable command error and stops observing', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    const approved = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    resourceOverrides['/sessions/session-1/state'] = approved;
    resourceOverrides['/sessions/session-1/commands'] = {
      command_id: 'decompose-job-1', status: 'pending',
      status_url: '/sessions/session-1/commands/decompose-job-1',
      events_url: '/sessions/session-1/commands/decompose-job-1/events',
    };
    resourceOverrides['/sessions/session-1/commands/decompose-job-1'] = {
      command_id: 'decompose-job-1', status: 'failed', status_version: 3,
      result: null, error: { code: 'AGENT_UNAVAILABLE', message: 'An Agent operation is unavailable; retry the workflow action.' },
      created_at: '2026-09-05T00:00:00Z', started_at: '2026-09-05T00:00:01Z', completed_at: '2026-09-05T00:00:10Z',
    };
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    const decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));
    vi.useFakeTimers();
    fireEvent.click(decompose);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(5_000);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getAllByText(/AGENT_UNAVAILABLE：An Agent operation is unavailable/)).not.toHaveLength(0);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(commandStatusRequestCount()).toBe(1);
  });

  it('drops a stale decomposition receipt after the session already advanced', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    localStorage.setItem('firstflight.decomposition-job.session-1', 'saved-job');
    resourceOverrides['/sessions/session-1/state'] = {
      ...state,
      phase: 'AGENT_SPECS_READY',
      state_version: 7,
      current_spec_status: 'APPROVED',
      legal_actions: ['start_task', 'complete_task', 'fail_task'],
      next_action: 'EXECUTE_WORK_ITEMS',
      review_findings: [],
    } satisfies SessionStateDto;
    resourceOverrides['/sessions/session-1/commands/saved-job'] = {
      command_id: 'saved-job', status: 'failed', status_version: 4,
      result: null,
      error: { code: 'PROCESS_INTERRUPTED', message: 'The background command process was interrupted; retry the command.' },
      created_at: '2026-09-05T00:00:00Z', started_at: '2026-09-05T00:00:01Z', completed_at: '2026-09-05T00:00:10Z',
    };

    render(<ApiWorkspace />);

    await waitFor(() => expect(screen.getByText('任务规格已就绪')).toBeTruthy());
    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBeNull();
    expect(getRequestCount('/sessions/session-1/commands/saved-job')).toBe(1);
    expect(screen.queryByText(/PROCESS_INTERRUPTED/)).toBeNull();
  });

  it('closes SSE and cancels polling when the workspace unmounts', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    const approved = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    resourceOverrides['/sessions/session-1/state'] = approved;
    resourceOverrides['/sessions/session-1/commands'] = {
      command_id: 'decompose-job-1', status: 'pending',
      status_url: '/sessions/session-1/commands/decompose-job-1',
      events_url: '/sessions/session-1/commands/decompose-job-1/events',
    };
    resourceOverrides['/sessions/session-1/commands/decompose-job-1'] = {
      command_id: 'decompose-job-1', status: 'processing', status_version: 2,
      result: null, error: null, created_at: '2026-09-05T00:00:00Z', started_at: '2026-09-05T00:00:01Z', completed_at: null,
    };
    const view = render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    const decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));
    vi.useFakeTimers();
    fireEvent.click(decompose);
    await vi.advanceTimersByTimeAsync(0);
    expect(WorkspaceEventSource.instances).toHaveLength(1);

    view.unmount();
    expect(WorkspaceEventSource.instances[0].close).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(commandStatusRequestCount()).toBe(0);
  });

  it('closes an active decomposition observer when the user switches projects', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    sessionCatalog.push({session_id:'session-2',project_id:'project-2',root_work_item_id:'root-2',title:'客户支持助手'});
    const approved = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    resourceOverrides = {
      '/sessions/session-1/state': approved,
      '/sessions/session-1/commands': {
        command_id: 'decompose-job-1', status: 'pending',
        status_url: '/sessions/session-1/commands/decompose-job-1',
        events_url: '/sessions/session-1/commands/decompose-job-1/events',
      },
      '/sessions/session-1/commands/decompose-job-1': {
        command_id: 'decompose-job-1', status: 'processing', status_version: 2,
        result: null, error: null, created_at: '2026-09-05T00:00:00Z', started_at: '2026-09-05T00:00:01Z', completed_at: null,
      },
      '/sessions/session-2/state':{...state,session_id:'session-2',project_id:'project-2',current_spec_version_id:null,legal_actions:[],phase:'NEED_CLARIFICATION'},
      '/sessions/session-2/specs':[],
      '/sessions/session-2/work-items':[{id:'root-2',kind:'ROOT',title:'客户支持助手',parent_id:null,dependency_work_item_ids:[]}],
      '/sessions/session-2/agent-specs':[],
      '/sessions/session-2/events':[],
    };
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    const decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));
    vi.useFakeTimers();
    fireEvent.click(decompose);
    await vi.advanceTimersByTimeAsync(0);
    expect(WorkspaceEventSource.instances).toHaveLength(1);

    fireEvent.change(screen.getByRole('combobox', { name: '当前对话项目' }), { target: { value: 'session-2' } });
    expect(WorkspaceEventSource.instances[0].close).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(commandStatusRequestCount()).toBe(0);
  });

  it('retains terminal recovery and does not select an old task when refresh finishes after a project switch', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    sessionCatalog.push({session_id:'session-2',project_id:'project-2',root_work_item_id:'root-2',title:'客户支持助手'});
    const approved = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    resourceOverrides = {
      '/sessions/session-1/state': approved,
      '/sessions/session-1/commands': {
        command_id: 'decompose-job-1', status: 'pending',
        status_url: '/sessions/session-1/commands/decompose-job-1',
        events_url: '/sessions/session-1/commands/decompose-job-1/events',
      },
      '/sessions/session-1/commands/decompose-job-1': {
        command_id: 'decompose-job-1', status: 'succeeded', status_version: 3,
        result: {
          command_id: 'decompose-job-1',
          state: { ...approved, phase: 'AGENT_SPECS_READY', state_version: 7 },
          created_resource_ids: ['task-todo'],
        },
        error: null, created_at: '2026-09-05T00:00:00Z',
        started_at: '2026-09-05T00:00:01Z', completed_at: '2026-09-05T00:00:10Z',
      },
      '/sessions/session-2/state':{...state,session_id:'session-2',project_id:'project-2',current_spec_version_id:null,legal_actions:[],phase:'NEED_CLARIFICATION'},
      '/sessions/session-2/specs':[],
      '/sessions/session-2/work-items':[{id:'root-2',kind:'ROOT',title:'客户支持助手',parent_id:null,dependency_work_item_ids:[]}],
      '/sessions/session-2/agent-specs':[],
      '/sessions/session-2/events':[],
    };
    render(<ApiWorkspace />);
    const decompose = await screen.findByRole('button', { name: '继续拆分子任务' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));
    const delayedRefresh = deferred();
    resourceDelaySequences['/sessions/session-1/state'] = [delayedRefresh.promise];
    vi.useFakeTimers();

    fireEvent.click(decompose);
    await vi.advanceTimersByTimeAsync(5_000);
    await vi.advanceTimersByTimeAsync(0);
    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBe('decompose-job-1');

    const projectPicker = screen.getByRole('combobox', { name: '当前对话项目' });
    fireEvent.change(projectPicker, { target: { value: 'session-2' } });
    delayedRefresh.resolve();
    await vi.advanceTimersByTimeAsync(0);

    expect((projectPicker as HTMLSelectElement).value).toBe('session-2');
    expect(screen.queryByRole('dialog', { name: '任务详情' })).toBeNull();
    expect(screen.queryByText(/REQUEST_ABORTED|NETWORK_ERROR/)).toBeNull();
    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBe('decompose-job-1');
  });

  it('does not surface a late old-project refresh error after switching projects', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    sessionCatalog.push({session_id:'session-2',project_id:'project-2',root_work_item_id:'root-2',title:'客户支持助手'});
    const approved = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    resourceOverrides = {
      '/sessions/session-1/state': approved,
      '/sessions/session-1/commands': {
        command_id: 'decompose-job-1', status: 'pending',
        status_url: '/sessions/session-1/commands/decompose-job-1',
        events_url: '/sessions/session-1/commands/decompose-job-1/events',
      },
      '/sessions/session-1/commands/decompose-job-1': {
        command_id: 'decompose-job-1', status: 'succeeded', status_version: 3,
        result: {
          command_id: 'decompose-job-1',
          state: { ...approved, phase: 'AGENT_SPECS_READY', state_version: 7 },
          created_resource_ids: ['task-todo'],
        },
        error: null, created_at: '2026-09-05T00:00:00Z',
        started_at: '2026-09-05T00:00:01Z', completed_at: '2026-09-05T00:00:10Z',
      },
      '/sessions/session-2/state':{...state,session_id:'session-2',project_id:'project-2',current_spec_version_id:null,legal_actions:[],phase:'NEED_CLARIFICATION'},
      '/sessions/session-2/specs':[],
      '/sessions/session-2/work-items':[{id:'root-2',kind:'ROOT',title:'客户支持助手',parent_id:null,dependency_work_item_ids:[]}],
      '/sessions/session-2/agent-specs':[],
      '/sessions/session-2/events':[],
    };
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    const decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));
    const delayedRefresh = deferred();
    resourceDelaySequences['/sessions/session-1/state'] = [delayedRefresh.promise];
    vi.useFakeTimers();

    fireEvent.click(decompose);
    await vi.advanceTimersByTimeAsync(5_000);
    await vi.advanceTimersByTimeAsync(0);
    fireEvent.change(screen.getByRole('combobox', { name: '当前对话项目' }), { target: { value: 'session-2' } });
    const oldRefreshRequest = fetchSpy.mock.calls.filter(([input]) =>
      new URL(String(input)).pathname === '/sessions/session-1/state').at(-1);
    expect(oldRefreshRequest?.[1]?.signal?.aborted).toBe(true);
    delayedRefresh.reject(new Error('late old-project refresh failure'));
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.queryByText(/NETWORK_ERROR/)).toBeNull();
    expect(screen.queryByText(/late old-project refresh failure/)).toBeNull();
    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBe('decompose-job-1');
  });

  it('keeps terminal recovery after unmount when a delayed refresh later succeeds', async () => {
    localStorage.setItem('firstflight.active-session-id', 'session-1');
    const approved = {
      ...state,
      current_spec_status: 'APPROVED' as const,
      legal_actions: ['convert_to_work_item'] as SessionStateDto['legal_actions'],
    };
    resourceOverrides['/sessions/session-1/state'] = approved;
    resourceOverrides['/sessions/session-1/commands'] = {
      command_id: 'decompose-job-1', status: 'pending',
      status_url: '/sessions/session-1/commands/decompose-job-1',
      events_url: '/sessions/session-1/commands/decompose-job-1/events',
    };
    resourceOverrides['/sessions/session-1/commands/decompose-job-1'] = {
      command_id: 'decompose-job-1', status: 'succeeded', status_version: 3,
      result: {
        command_id: 'decompose-job-1',
        state: { ...approved, phase: 'AGENT_SPECS_READY', state_version: 7 },
        created_resource_ids: ['task-todo'],
      },
      error: null, created_at: '2026-09-05T00:00:00Z',
      started_at: '2026-09-05T00:00:01Z', completed_at: '2026-09-05T00:00:10Z',
    };
    const view = render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /知识问答.*打开 PRD 审核/ }));
    const dialog = within(await screen.findByRole('dialog', { name: 'PRD 审核' }));
    const decompose = dialog.getByRole('button', { name: '开始任务拆分' });
    await waitFor(() => expect((decompose as HTMLButtonElement).disabled).toBe(false));
    const delayedRefresh = deferred();
    resourceDelaySequences['/sessions/session-1/state'] = [delayedRefresh.promise];
    vi.useFakeTimers();

    fireEvent.click(decompose);
    await vi.advanceTimersByTimeAsync(5_000);
    await vi.advanceTimersByTimeAsync(0);
    view.unmount();
    delayedRefresh.resolve();
    await vi.advanceTimersByTimeAsync(0);

    expect(localStorage.getItem('firstflight.decomposition-job.session-1')).toBe('decompose-job-1');
  });
});

it('uses the current PRD version as the count without downloading every historical XML payload', async () => {
  renderPanel();

  expect(await screen.findByText(/1 个版本/)).toBeTruthy();
  expect(getRequestCount('/prd/root-1/versions')).toBe(0);
});

it('shows the complete rendered PRD alongside a diff that includes removed lines', async () => {
  renderPanel();
  fireEvent.click(screen.getByRole('button',{name:'并排查看'}));
  const body = await screen.findByRole('region',{name:'PRD 正文'});
  expect(within(body).getByRole('heading',{name:'需求说明'})).toBeTruthy();
  expect(within(body).getByText('这是已保存的 PRD 正文。')).toBeTruthy();
  const diff = await screen.findByRole('region',{name:'PRD Diff'});
  expect(within(diff).getByText('Diff · v1 初始版本')).toBeTruthy();
  expect(await within(diff).findByText('-旧要求')).toBeTruthy();
  const addedLine = within(diff).getByText('+这是已保存的 PRD 正文。');
  expect(addedLine).toBeTruthy();
  expect(addedLine.parentElement?.className).toContain('text-slate-900');
  expect(within(diff).queryByRole('button',{name:/旧要求/})).toBeNull();
});

it('shows an unchanged PRD as white commentable full-document diff rows', async () => {
  resourceOverrides = {
    '/prd/root-1/diff':{wi:'root-1',version:2,filename:'docs/prd/root-1/v2.md',commit_sha:'def456',patch:''},
    '/prd/root-1':{...doc,version:2,filename:'docs/prd/root-1/v2.md',commit_sha:'def456'},
    '/prd/root-1/commentable-lines':{wi:'root-1',version:2,filename:'docs/prd/root-1/v2.md',commit_sha:'def456',lines:[{line:1,kind:'addition',text:'# 需求说明'},{line:2,kind:'addition',text:'这是已保存的 PRD 正文。'}]},
  };
  renderPanel();

  const diff = await screen.findByRole('region',{name:'PRD Diff'});
  expect(within(diff).getByText('Diff · v2 对比 v1')).toBeTruthy();
  expect(within(diff).getByText(/以下显示完整正文/)).toBeTruthy();
  const firstLine = within(diff).getByRole('button',{name:'给第 1 行添加批注'});
  expect(firstLine.className).toContain('bg-white');
  fireEvent.click(firstLine);
  expect(screen.getByPlaceholderText('给第 1 行添加批注')).toBeTruthy();
});

it('round-trips a draw.io save into a polled PRD revision and clears the draft on success', async () => {
  const diagram = {
    diagram_id:'data-model', title:'核心数据模型', after_section:'core_objects' as const,
    anchor:'firstflight-er-data-model-deadbeef',
    drawio_xml:'<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>',
  };
  const diagramLink = `[ER 图：核心数据模型](#${diagram.anchor})`;
  const diagramDocument = {...doc,content:`# 需求说明\n\n${diagramLink}`,er_diagrams:[diagram]};
  resourceOverrides = {
    '/prd/root-1':diagramDocument,
    '/prd/root-1/versions':[diagramDocument],
    '/prd/root-1/diff':{wi:'root-1',version:1,filename:doc.filename,commit_sha:'abc123',patch:`@@ -0,0 +1,3 @@\n+# 需求说明\n+\n+${diagramLink}\n`},
    '/prd/root-1/diagrams/data-model/revisions':{task_id:'diagram-task-1',base_version:1,no_change:false},
    '/tasks/diagram-task-1':{task_id:'diagram-task-1',wi:'root-1',status:'done',base_version:1,new_version:2,new_commit_sha:'def456',error:null},
  };
  window.GraphViewer = { createViewerForElement: (element) => { element.textContent = 'rendered graph'; } };
  const popup = { closed:false, postMessage:vi.fn(), close:vi.fn() } as unknown as Window;
  vi.spyOn(window,'open').mockReturnValue(popup);
  vi.spyOn(window,'prompt').mockReturnValue('调整订单关系');
  renderPanel();
  fireEvent.click(screen.getByRole('button',{name:'正文'}));
  fireEvent.click(await screen.findByRole('button',{name:'在 draw.io 中编辑'}));
  window.dispatchEvent(new MessageEvent('message',{origin:'https://embed.diagrams.net',source:popup,data:JSON.stringify({event:'init'})}));
  const revisedXml = diagram.drawio_xml.replace('<root/>','<root><mxCell id="0"/></root>');
  window.dispatchEvent(new MessageEvent('message',{origin:'https://embed.diagrams.net',source:popup,data:JSON.stringify({event:'save',xml:revisedXml})}));

  await waitFor(() => expect(fetchSpy.mock.calls.some(([input,options]) =>
    options?.method === 'POST' && new URL(String(input)).pathname === '/prd/root-1/diagrams/data-model/revisions'
  )).toBe(true));
  const revisionCall = fetchSpy.mock.calls.find(([input,options]) =>
    options?.method === 'POST' && new URL(String(input)).pathname === '/prd/root-1/diagrams/data-model/revisions'
  );
  expect(JSON.parse(String(revisionCall?.[1]?.body))).toEqual({
    base_version:1, base_commit_sha:'abc123', drawio_xml:revisedXml, change_summary:'调整订单关系',
  });
  await waitFor(() => expect(localStorage.getItem('firstflight.prd-task.root-1')).toBe('diagram-task-1'));
  await waitFor(() => expect(screen.queryByText(/尚未确认写入新版 PRD/)).toBeNull(),{timeout:2_000});
  expect(popup.postMessage).toHaveBeenCalledWith(JSON.stringify({action:'exit'}),'https://embed.diagrams.net');
});

it('retains and restores the exact draw.io draft after a stale-version conflict', async () => {
  const diagram = {
    diagram_id:'data-model', title:'核心数据模型', after_section:'core_objects' as const,
    anchor:'firstflight-er-data-model-deadbeef',
    drawio_xml:'<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>',
  };
  const diagramLink = `[ER 图：核心数据模型](#${diagram.anchor})`;
  const diagramDocument = {...doc,content:`# 需求说明\n\n${diagramLink}`,er_diagrams:[diagram]};
  resourceOverrides = {
    '/prd/root-1':diagramDocument,
    '/prd/root-1/versions':[diagramDocument],
  };
  resourceOverrideSequences['/prd/root-1/diagrams/data-model/revisions'] = [
    new Response(JSON.stringify({detail:{code:'PRD_CONTENT_CONFLICT',message:'stale'}}),{status:409}),
    new Response(JSON.stringify({detail:{code:'PRD_CONTENT_CONFLICT',message:'still stale'}}),{status:409}),
  ];
  window.GraphViewer = { createViewerForElement: (element) => { element.textContent = 'rendered graph'; } };
  const popup = { closed:false, postMessage:vi.fn(), close:vi.fn() } as unknown as Window;
  vi.spyOn(window,'open').mockReturnValue(popup);
  vi.spyOn(window,'prompt').mockReturnValue('调整实体布局');
  const view = renderPanel();
  fireEvent.click(screen.getByRole('button',{name:'正文'}));
  fireEvent.click(await screen.findByRole('button',{name:'在 draw.io 中编辑'}));
  const revisedXml = diagram.drawio_xml.replace('<root/>','<root><mxCell id="0"/></root>');
  window.dispatchEvent(new MessageEvent('message',{origin:'https://embed.diagrams.net',source:popup,data:JSON.stringify({event:'save',xml:revisedXml})}));

  expect(await screen.findByText(/ER 图基于旧版 PRD/)).toBeTruthy();
  expect(screen.getByRole('button',{name:'重试提交 ER 图草稿'})).toBeTruthy();
  expect(screen.getByRole('button',{name:'下载 ER 图草稿'})).toBeTruthy();
  expect(popup.postMessage).not.toHaveBeenCalledWith(JSON.stringify({action:'exit'}),'https://embed.diagrams.net');
  expect(Object.keys(localStorage).some((key) => key.startsWith('firstflight.drawio-draft.'))).toBe(true);

  fireEvent.click(screen.getByRole('button',{name:'重试提交 ER 图草稿'}));
  await waitFor(() => expect(fetchSpy.mock.calls.filter(([input,options]) =>
    options?.method === 'POST' && new URL(String(input)).pathname === '/prd/root-1/diagrams/data-model/revisions'
  )).toHaveLength(2));
  expect(window.prompt).toHaveBeenCalledTimes(1);
  const revisionBodies = fetchSpy.mock.calls
    .filter(([input,options]) => options?.method === 'POST' && new URL(String(input)).pathname === '/prd/root-1/diagrams/data-model/revisions')
    .map(([,options]) => JSON.parse(String(options?.body)));
  expect(revisionBodies.map((body) => body.change_summary)).toEqual(['调整实体布局','调整实体布局']);

  view.unmount();
  renderPanel();
  expect(await screen.findByText(/尚未确认写入新版 PRD/)).toBeTruthy();
});
