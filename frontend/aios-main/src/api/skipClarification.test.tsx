// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ApiWorkspace } from './ApiWorkspace';
import type { CommandAction, SessionStateDto, SpecVersionDto, WorkItemDto } from './dto';

const reviewClarification: SessionStateDto = {
  session_id: 'session-1', project_id: 'project-1', phase: 'REVIEW', state_version: 6,
  current_spec_version_id: 'spec-current', current_spec_status: 'NEED_CLARIFICATION',
  legal_actions: ['message', 'restore_spec_version'], next_action: 'ANSWER_CLARIFICATION',
  outstanding_questions: [{question_id: 'q-1', question: '是否需要数据库？', reason: '明确范围', affected_areas: ['scope'], blocking: true}],
  review_findings: [{code: 'SCOPE-001', message: '请确认数据库范围。', blocks_progress: true}],
};
const initialClarification: SessionStateDto = {
  ...reviewClarification, phase: 'NEED_CLARIFICATION', state_version: 1,
  current_spec_version_id: null, current_spec_status: null,
  legal_actions: ['message', 'skip_clarification'], review_findings: [],
};
const readyToGenerate: SessionStateDto = {
  ...initialClarification, phase: 'SPECIFICATION', state_version: 3,
  legal_actions: ['create_spec'], next_action: 'CREATE_SPEC', outstanding_questions: [],
};
const generated: SessionStateDto = {
  ...reviewClarification, state_version: 5, current_spec_status: 'HUMAN_REVIEW',
  legal_actions: ['approve'], next_action: 'HUMAN_REVIEW', outstanding_questions: [], review_findings: [],
};
const spec: SpecVersionDto = {
  id: 'spec-current', project_id: 'project-1', revision: 2, content: {},
  markdown: '# 当前 PRD\n保留这一版的正文。', generation_source: 'agent',
  parent_version_id: 'spec-old', change_summary: '已保存的当前版本', status: 'NEED_CLARIFICATION',
  created_at: '2026-09-03T02:44:00Z', reviews: [],
};
const root: WorkItemDto = {
  id: 'root-1', parent_id: null, kind: 'ROOT', title: '知识问答', description: null,
  objective: '回答问题', scope: [], exclusions: [], outputs: null, acceptance_criteria: null,
  required_skills: null, responsible_role: 'Owner', suggested_assignee: 'owner-1',
  dependency_work_item_ids: [],
};
const doc = {wi: 'root-1', version: 2, filename: 'docs/prd/root-1/v2.md', pr_number: 1, commit_sha: 'abc123', content: spec.markdown, change_summary: spec.change_summary};
type Command = {command_id: string; action: CommandAction; expected_state_version: number; payload: Record<string, unknown>; message?: string};
type Reply = {state: SessionStateDto};

function mockHttp(initial: SessionStateDto, replies: Reply[]) {
  let serverState = initial;
  const commands: Command[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input)).pathname;
    if (path === '/sessions/session-1/commands' && init?.method === 'POST') {
      const command = JSON.parse(String(init.body)) as Command;
      commands.push(command);
      const reply = replies[commands.length - 1];
      if (!reply) throw new Error(`Unexpected command: ${command.action}`);
      serverState = reply.state;
      return Response.json({command_id: command.command_id, state: serverState, created_resource_ids: []});
    }
    const payloads: Record<string, unknown> = {
      '/healthz': {status: 'ok', database: 'ok'},
      '/sessions': [{session_id: 'session-1', project_id: 'project-1', root_work_item_id: root.id, title: root.title}],
      '/sessions/session-1/state': serverState,
      '/sessions/session-1/specs': serverState.current_spec_version_id ? [{...spec, status: serverState.current_spec_status}] : [],
      '/sessions/session-1/work-items': [root],
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
  it('does not offer a skip-to-approval path after a PRD already exists', async () => {
    mockHttp(reviewClarification, []);
    render(<ApiWorkspace />);
    const sidebar = screen.getByRole('complementary');
    expect(await within(sidebar).findByText('请回答下方问题，帮助系统明确需求；确认信息后会生成 PRD。')).toBeTruthy();
    expect(within(sidebar).queryByRole('button', {name: /跳过澄清/})).toBeNull();
    expect(within(sidebar).getByRole('button', {name: '提交澄清'})).toBeTruthy();
  });

  it('keeps initial clarification skip limited to generating the first PRD', async () => {
    const commands = mockHttp(initialClarification, [{state: readyToGenerate}, {state: generated}]);
    render(<ApiWorkspace />);
    fireEvent.click(await screen.findByRole('button', {name: '跳过澄清并生成 PRD'}));
    await within(screen.getByRole('complementary')).findByText('等待人工审核');
    await waitFor(() => expect(commands).toHaveLength(2));
    expect(commands).toEqual([
      {command_id: expect.any(String), action: 'skip_clarification', expected_state_version: 1, payload: {}},
      {command_id: expect.any(String), action: 'create_spec', expected_state_version: 3, payload: {}},
    ]);
    expect(commands.some((command) => command.action === 'convert_to_work_item')).toBe(false);
  });
});
