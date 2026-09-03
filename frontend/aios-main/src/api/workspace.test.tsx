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
let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  // jsdom has no native dialog implementation; exercise visibility here and native focus in browser QA.
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  failLines = false; failComments = false; failDocument = false; failDiff = false;
  localStorage.clear();
  fetchSpy = vi.fn(async (input: string | URL | Request) => {
    const path = new URL(String(input)).pathname;
    if ((failLines && path.endsWith('/commentable-lines')) || (failComments && path.endsWith('/comments')) || (failDocument && path === '/prd/root-1') || (failDiff && path.endsWith('/diff'))) {
      return new Response(JSON.stringify({detail:{code:'GITEA_UNAVAILABLE',message:'Gitea is temporarily unavailable.'}}), {status:503});
    }
    const payloads: Record<string, unknown> = {
      '/healthz':{status:'ok',database:'ok'},
      '/sessions/session-1/state':state,
      '/sessions/session-1/specs':[spec],
      '/sessions/session-1/work-items':[{id:'root-1',parent_id:null,kind:'ROOT',title:'知识问答',description:null,objective:'回答问题',scope:[],exclusions:[],outputs:null,acceptance_criteria:null,required_skills:null,responsible_role:'Owner',suggested_assignee:'owner-1',dependency_work_item_ids:[]}],
      '/sessions/session-1/agent-specs':[],
      '/sessions/session-1/events':[{id:'event-1',event_type:'AGENT_TRACE',actor_id:null,created_at:'2026-09-03T02:45:00',payload:{trace_id:'command-1',agent_call_id:'review-call-1',phase:'review_spec',status:'done',summary:'Reviewing specification quality',input_hash:'input-proof',output_hash:'output-proof',started_at:'2026-09-03T02:45:00',completed_at:'2026-09-03T02:46:00',safe_error_code:null}}],
      '/prd/root-1':doc,
      '/prd/root-1/diff':{wi:'root-1',version:1,filename:doc.filename,commit_sha:'abc123',patch:'@@ -1,2 +1,2 @@\n # 需求说明\n-旧要求\n+这是已保存的 PRD 正文。\n'},
      '/prd/root-1/versions':[doc],
      '/prd/root-1/comments':[],
      '/prd/root-1/commentable-lines':{wi:'root-1',version:1,filename:doc.filename,commit_sha:'abc123',lines:[{line:1,kind:'addition',text:'# 需求说明'},{line:2,kind:'addition',text:'这是已保存的 PRD 正文。'}]},
    };
    if (!(path in payloads)) throw new Error(`Unexpected test request: ${path}`);
    return new Response(JSON.stringify(payloads[path]), {status:200});
  });
  vi.stubGlobal('fetch',fetchSpy);
});
afterEach(() => {cleanup();localStorage.clear();vi.unstubAllGlobals();});

function renderPanel() {
  return render(<PrdReviewPanel wi="root-1" sessionState={state} fallbackSpec={spec} onConfirmAndDecompose={async () => undefined} />);
}

describe('workspace regression', () => {
  it.each(['lines','comments','document','diff'])('keeps saved PRD readable and blocks decisions when %s cannot load', async (resource) => {
    failLines = resource === 'lines'; failComments = resource === 'comments'; failDocument = resource === 'document'; failDiff = resource === 'diff';
    renderPanel();
    fireEvent.click(screen.getByRole('button',{name:'正文'}));
    expect(await within(screen.getByRole('region',{name:'PRD 正文'})).findByText('这是已保存的 PRD 正文。')).toBeTruthy();
    await screen.findByRole('alert');
    const approve = screen.queryByRole('button',{name:'确认 PRD 并开始任务拆解'});
    expect(!approve || (approve as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('button',{name:'发布批注并生成新版'})).toBeNull();
    failLines = false; failComments = false; failDocument = false; failDiff = false;
    fireEvent.click(screen.getByRole('button',{name:'重试加载'}));
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
