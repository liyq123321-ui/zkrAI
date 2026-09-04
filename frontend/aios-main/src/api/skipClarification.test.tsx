// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ApiWorkspace } from './ApiWorkspace';
import type { CommandAction, SessionStateDto, SpecVersionDto, WorkItemDto } from './dto';

const review: SessionStateDto = {
  session_id: 'session-1', project_id: 'project-1', phase: 'REVIEW', state_version: 6,
  current_spec_version_id: 'spec-current', current_spec_status: 'NEED_CLARIFICATION',
  // The API's legal-action order must not move skip above submit.
  legal_actions: ['skip_clarification', 'message', 'restore_spec_version'],
  next_action: 'ANSWER_CLARIFICATION',
  outstanding_questions: [{question_id: 'q-1', question: '是否需要数据库？', reason: '明确范围', affected_areas: ['scope'], blocking: true}],
  review_findings: [{code: 'SCOPE-001', message: '请确认数据库范围。', blocks_progress: true}],
};
const approved: SessionStateDto = {
  ...review, state_version: 9, current_spec_status: 'APPROVED',
  legal_actions: ['convert_to_work_item'], next_action: 'CONVERT_TO_WORK_ITEM', outstanding_questions: [],
};
const decomposed: SessionStateDto = {
  ...approved, state_version: 12, phase: 'AGENT_SPECS_READY',
  legal_actions: [], next_action: 'NONE',
};
const initialClarification: SessionStateDto = {
  ...review, phase: 'NEED_CLARIFICATION', state_version: 1, current_spec_version_id: null,
  current_spec_status: null, legal_actions: ['message', 'skip_clarification'], review_findings: [],
};
const readyToGenerate: SessionStateDto = {
  ...initialClarification, phase: 'SPECIFICATION', state_version: 3, legal_actions: ['create_spec'], next_action: 'CREATE_SPEC', outstanding_questions: [],
};
const generated: SessionStateDto = {
  ...review, state_version: 5, current_spec_status: 'HUMAN_REVIEW',
  legal_actions: ['approve'], next_action: 'HUMAN_REVIEW', outstanding_questions: [], review_findings: [],
};
const spec: SpecVersionDto = {
  id: 'spec-current', project_id: 'project-1', revision: 2, content: {}, markdown: '# 当前 PRD\n保留这一版的正文。',
  generation_source: 'agent', parent_version_id: 'spec-old', change_summary: '已保存的当前版本',
  status: 'NEED_CLARIFICATION', created_at: '2026-09-03T02:44:00Z',
  reviews: [{id: 'review-1', kind: 'SEMANTIC', verdict: 'NEED_CLARIFICATION', findings: review.review_findings, comments: null, created_at: '2026-09-03T02:46:00Z'}],
};
const root: WorkItemDto = {
  id: 'root-1', parent_id: null, kind: 'ROOT', title: '知识问答', description: null, objective: '回答问题',
  scope: [], exclusions: [], outputs: null, acceptance_criteria: null, required_skills: null,
  responsible_role: 'Owner', suggested_assignee: 'owner-1', dependency_work_item_ids: [],
};
const task: WorkItemDto = {...root, id: 'task-1', parent_id: 'root-1', kind: 'TASK', title: '实现问答功能'};
const doc = {wi: 'root-1', version: 2, filename: 'docs/prd/root-1/v2.md', pr_number: 1, commit_sha: 'abc123', content: spec.markdown, change_summary: spec.change_summary};
const skipLabel = '跳过澄清，确认当前 PRD 并拆分子任务';
type Command = {command_id: string; action: CommandAction; expected_state_version: number; payload: Record<string, unknown>; message?: string};
type Reply = {state: SessionStateDto} | {error: string; status: number};

function deferredReply() {
  let resolve!: (reply: Reply) => void;
  const promise = new Promise<Reply>((done) => { resolve = done; });
  return {promise, resolve};
}

// Keep the components, API client, and command serialization real; mock HTTP only.
function mockHttp(initial: SessionStateDto, replies: Array<Reply | Promise<Reply>>, {giteaUnavailable = false} = {}) {
  let serverState = initial;
  const commands: Command[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input)).pathname;
    if (path === '/sessions/session-1/commands' && init?.method === 'POST') {
      const command = JSON.parse(String(init.body)) as Command;
      commands.push(command);
      const reply = await replies[commands.length - 1];
      if (!reply) throw new Error(`Unexpected command: ${command.action}`);
      if ('error' in reply) return Response.json({detail: {code: reply.error, message: '暂时无法执行，请重试。'}}, {status: reply.status});
      serverState = reply.state;
      return Response.json({command_id: command.command_id, state: serverState, created_resource_ids: []});
    }
    if (init?.method && init.method !== 'GET') throw new Error(`Unexpected mutation: ${path}`);
    if (giteaUnavailable && path.startsWith('/prd/')) {
      return Response.json({detail: {code: 'GITEA_UNAVAILABLE', message: 'Gitea is temporarily unavailable.'}}, {status: 503});
    }
    const payloads: Record<string, unknown> = {
      '/healthz': {status: 'ok', database: 'ok'},
      '/sessions': [{session_id:'session-1',project_id:'project-1',root_work_item_id:root.id,title:root.title}],
      '/sessions/session-1/state': serverState,
      '/sessions/session-1/specs': serverState.current_spec_version_id ? [{...spec, status: serverState.current_spec_status}] : [],
      '/sessions/session-1/work-items': serverState.phase === 'AGENT_SPECS_READY' ? [root, task] : [root],
      '/sessions/session-1/agent-specs': [], '/sessions/session-1/events': [],
      '/prd/root-1': doc, '/prd/root-1/versions': [doc], '/prd/root-1/comments': [],
      '/prd/root-1/diff': {wi: 'root-1', version: 2, filename: doc.filename, commit_sha: 'abc123', patch: '@@ -1 +1 @@\n+# 当前 PRD\n'},
      '/prd/root-1/commentable-lines': {wi: 'root-1', version: 2, filename: doc.filename, commit_sha: 'abc123', lines: [{line: 1, kind: 'addition', text: '# 当前 PRD'}]},
    };
    if (!(path in payloads)) throw new Error(`Unexpected request: ${path}`);
    return Response.json(payloads[path]);
  }));
  return commands;
}

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  localStorage.clear();
  localStorage.setItem('firstflight.active-session-id', 'session-1');
});
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });

describe('skip clarification through the workspace', () => {
  it('explains that review clarification can confirm the current PRD and decompose it', async () => {
    mockHttp(review, []);
    render(<ApiWorkspace />);
    const sidebar = screen.getByRole('complementary');
    expect(await within(sidebar).findByText('可补充下方澄清问题；也可跳过澄清，确认当前 PRD 并直接拆分子任务。')).toBeTruthy();
    expect(within(sidebar).queryByText(/确认信息后会生成 PRD/)).toBeNull();
  });

  it('places explicit current-PRD approval below submit and decomposes with the returned state version', async () => {
    const commands = mockHttp(review, [{state: approved}, {state: decomposed}]);
    render(<ApiWorkspace />);
    const skip = await screen.findByRole('button', {name: skipLabel});
    expect(screen.getByRole('button', {name: '提交澄清'}).nextElementSibling).toBe(skip);
    expect(screen.queryByRole('button', {name: '跳过澄清并生成 PRD'})).toBeNull();
    fireEvent.click(skip);
    await screen.findByRole('button', {name: /实现问答功能/});
    expect(commands).toEqual([
      {command_id: expect.any(String), action: 'skip_clarification', expected_state_version: 6, payload: {confirm_current_spec: true, spec_version_id: 'spec-current'}},
      {command_id: expect.any(String), action: 'convert_to_work_item', expected_state_version: 9, payload: {}},
    ]);
    fireEvent.click(screen.getByRole('button', {name: /知识问答.*打开 PRD 审核/}));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', {name: '正文'}));
    expect(await screen.findByText('保留这一版的正文。')).toBeTruthy();
  });

  it('disables repeat actions throughout approval and decomposition', async () => {
    const skipReply = deferredReply();
    const convertReply = deferredReply();
    const commands = mockHttp(review, [skipReply.promise, convertReply.promise]);
    render(<ApiWorkspace />);
    const skip = await screen.findByRole('button', {name: skipLabel});
    fireEvent.change(screen.getByLabelText('澄清答案'), {target: {value: '数据库不是必须的'}});
    fireEvent.click(skip);
    for (const button of [skip, screen.getByRole('button', {name: '提交澄清'}), screen.getByRole('button', {name: '刷新项目状态'}), screen.getByRole('button', {name: '返回项目入口（保留数据）'})]) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(button);
    }
    expect(commands).toHaveLength(1);
    await act(async () => { skipReply.resolve({state: approved}); });
    await waitFor(() => expect(commands).toHaveLength(2));
    fireEvent.click(screen.getByRole('button', {name: '查看 PRD 与审核意见'}));
    const retry = await screen.findByRole('button', {name: '开始任务拆解'});
    expect((retry as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(retry);
    expect(commands).toHaveLength(2);
    await act(async () => { convertReply.resolve({state: decomposed}); });
    await screen.findByRole('button', {name: /实现问答功能/});
    expect((screen.getByRole('button', {name: '刷新项目状态'}) as HTMLButtonElement).disabled).toBe(false);
  });

  it.each(['button', 'text'])('keeps initial NEED_CLARIFICATION %s skip generating its first PRD without an approval payload', async (trigger) => {
    const commands = mockHttp(initialClarification, [{state: readyToGenerate}, {state: generated}]);
    render(<ApiWorkspace />);
    const skip = await screen.findByRole('button', {name: '跳过澄清并生成 PRD'});
    expect(screen.getByRole('button', {name: '提交澄清'}).nextElementSibling).toBe(skip);
    if (trigger === 'text') {
      fireEvent.change(screen.getByLabelText('澄清答案'), {target: {value: '跳过澄清'}});
      fireEvent.click(screen.getByRole('button', {name: '提交澄清'}));
    } else fireEvent.click(skip);
    await within(screen.getByRole('complementary')).findByText('等待人工审核');
    expect(commands).toEqual([
      {command_id: expect.any(String), action: 'skip_clarification', expected_state_version: 1, payload: {}},
      {command_id: expect.any(String), action: 'create_spec', expected_state_version: 3, payload: {}},
    ]);
  });

  it.each([
    ['REVIEW', 'spec-current'], ['NEED_CLARIFICATION', 'spec-current'], ['REVIEW', null],
  ])('submits plain skip text as a message in %s with current PRD %s', async (phase, currentSpecId) => {
    const initial = {...review, phase: phase!, current_spec_version_id: currentSpecId};
    const commands = mockHttp(initial, [{state: {...initial, state_version: 7}}]);
    render(<ApiWorkspace />);
    fireEvent.change(await screen.findByLabelText('澄清答案'), {target: {value: '跳过澄清'}});
    fireEvent.click(screen.getByRole('button', {name: '提交澄清'}));
    await waitFor(() => expect((screen.getByRole('button', {name: '刷新项目状态'}) as HTMLButtonElement).disabled).toBe(false));
    expect(commands).toEqual([
      {command_id: expect.any(String), action: 'message', expected_state_version: 6, message: '跳过澄清', payload: {}},
    ]);
  });

  it('retains approval after decomposition fails and retries only decomposition from the PRD UI', async () => {
    const commands = mockHttp(review, [{state: approved}, {error: 'DECOMPOSITION_FAILED', status: 503}, {state: decomposed}]);
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', {name: skipLabel}));
    await screen.findByText(/DECOMPOSITION_FAILED/);
    await within(screen.getByRole('complementary')).findByText('已确认');
    expect(screen.queryByRole('button', {name: skipLabel})).toBeNull();
    fireEvent.click(screen.getByRole('button', {name: '查看 PRD 与审核意见'}));
    const retry = await screen.findByRole('button', {name: '开始任务拆解'});
    expect((retry as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(retry);
    await screen.findByRole('button', {name: /实现问答功能/});
    expect(commands).toEqual([
      {command_id: expect.any(String), action: 'skip_clarification', expected_state_version: 6, payload: {confirm_current_spec: true, spec_version_id: 'spec-current'}},
      {command_id: expect.any(String), action: 'convert_to_work_item', expected_state_version: 9, payload: {}},
      {command_id: commands[1].command_id, action: 'convert_to_work_item', expected_state_version: 9, payload: {}},
    ]);
    expect(screen.queryByText(/DECOMPOSITION_FAILED/)).toBeNull();
  });

  it('retries decomposition from the sidebar with the same command ID while Gitea is unavailable', async () => {
    const retryReply = deferredReply();
    const commands = mockHttp(review, [
      {state: approved}, {error: 'DECOMPOSITION_FAILED', status: 503}, retryReply.promise,
    ], {giteaUnavailable: true});
    render(<ApiWorkspace />);
    const skip = await screen.findByRole('button', {name: skipLabel});
    const sidebar = within(screen.getByRole('complementary'));
    expect(sidebar.queryByRole('button', {name: '继续拆分子任务'})).toBeNull();
    fireEvent.click(skip);
    await screen.findByText(/DECOMPOSITION_FAILED/);
    expect(sidebar.getByText('已确认')).toBeTruthy();

    fireEvent.click(sidebar.getByRole('button', {name: '查看 PRD 与审核意见'}));
    const dialog = within(await screen.findByRole('dialog'));
    expect((await dialog.findByRole('alert')).textContent).toContain('GITEA_UNAVAILABLE');
    const dialogRetry = dialog.getByRole('button', {name: '开始任务拆解'});
    expect((dialogRetry as HTMLButtonElement).disabled).toBe(true);
    expect(dialog.getByRole('checkbox')).toBeTruthy();
    fireEvent.click(dialog.getByRole('button', {name: '关闭详情'}));

    const retry = await sidebar.findByRole('button', {name: '继续拆分子任务'});
    expect((retry as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(retry);
    expect((retry as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(retry);
    expect(commands).toEqual([
      {command_id: expect.any(String), action: 'skip_clarification', expected_state_version: 6, payload: {confirm_current_spec: true, spec_version_id: 'spec-current'}},
      {command_id: expect.any(String), action: 'convert_to_work_item', expected_state_version: 9, payload: {}},
      {command_id: commands[1].command_id, action: 'convert_to_work_item', expected_state_version: 9, payload: {}},
    ]);
    await act(async () => { retryReply.resolve({state: decomposed}); });
    await screen.findByRole('button', {name: /实现问答功能/});
    expect(sidebar.getByText('任务规格已就绪')).toBeTruthy();
    expect(sidebar.queryByRole('button', {name: '继续拆分子任务'})).toBeNull();
    expect(screen.queryByText(/DECOMPOSITION_FAILED/)).toBeNull();
  });

  it('does not decompose after approval fails and leaves skip available to retry', async () => {
    const commands = mockHttp(review, [{error: 'FORBIDDEN', status: 403}]);
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', {name: skipLabel}));
    await screen.findByText(/FORBIDDEN/);
    expect(commands.map((command) => command.action)).toEqual(['skip_clarification']);
    expect((screen.getByRole('button', {name: skipLabel}) as HTMLButtonElement).disabled).toBe(false);
    expect(within(screen.getByRole('complementary')).queryByText('已确认')).toBeNull();
  });
});
