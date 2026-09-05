// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { AgentRuntimePanel } from './AgentRuntimePanel';

const runtime = [
  { agent_session_id:'agent-pm', project_id:'project-1', role:'PM Agent', provider:'codex', model:'gpt-test', purpose:'拆解需求', status:'running', current_operation:'decompose_spec', current_summary:'Decomposing the approved specification', current_call_id:'call-2', started_at:'2026-09-06T02:01:00Z', completed_at:null, call_count:2 },
  { agent_session_id:'agent-reviewer', project_id:'project-1', role:'Reviewer Agent', provider:'codex', model:null, purpose:'审核方案', status:'error', current_operation:'review_spec', current_summary:'Reviewing specification quality', current_call_id:'call-3', started_at:'2026-09-06T02:02:00Z', completed_at:'2026-09-06T02:03:00Z', call_count:1 },
  { agent_session_id:'agent-writer', project_id:'project-1', role:'Writer Agent', provider:'codex', model:'gpt-test', purpose:'编写规格', status:'completed', current_operation:'generate_spec', current_summary:'Generated the project specification', current_call_id:'call-4', started_at:'2026-09-06T02:04:00Z', completed_at:'2026-09-06T02:05:00Z', call_count:3 },
];

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    json: async () => body,
  } as Response;
}

beforeEach(() => vi.stubGlobal('fetch', vi.fn(async () =>
  new Response(JSON.stringify(runtime), { status:200 }))));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('AgentRuntimePanel', () => {
  it('summarizes and groups the latest Agent runtime snapshot', async () => {
    render(<AgentRuntimePanel projects={[{ sessionId:'session-1', title:'知识问答' }]} />);

    const panel = await screen.findByRole('region', { name:'Agent 实时运行状态' });
    const metrics = panel.querySelector<HTMLElement>('.ff-agent-runtime-metrics')!;
    const metricValue = (label: string) =>
      within(metrics).getByText(label).parentElement?.querySelector('dd')?.textContent;
    expect(metricValue('已启动')).toBe('3');
    expect(metricValue('运行中')).toBe('1');
    expect(metricValue('已完成')).toBe('1');
    expect(metricValue('异常')).toBe('1');
    expect(within(panel).getByText('知识问答')).toBeTruthy();
    expect(within(panel).getByText('PM Agent')).toBeTruthy();
    expect(within(panel).getByText('Reviewer Agent')).toBeTruthy();
    expect(within(panel).getByText('Writer Agent')).toBeTruthy();
    expect(within(panel).getByText('拆解已批准的项目规格')).toBeTruthy();
  });

  it('renders an explicit empty state', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('[]', { status:200 }));
    render(<AgentRuntimePanel projects={[{ sessionId:'session-1', title:'空项目' }]} />);

    expect(await screen.findByText('暂无已启动 Agent')).toBeTruthy();
  });

  it('polls after two seconds and stops after unmount', async () => {
    vi.useFakeTimers();
    const view = render(<AgentRuntimePanel projects={[{ sessionId:'session-1', title:'知识问答' }]} />);

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    view.unmount();
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('aborts each in-flight request when unmounted', async () => {
    vi.useFakeTimers();
    let resolveResponse: ((response: Response) => void) | undefined;
    const signals: AbortSignal[] = [];
    vi.mocked(fetch).mockImplementation((_input, init) => {
      signals.push(init!.signal as AbortSignal);
      return new Promise<Response>((resolve) => { resolveResponse = resolve; });
    });
    const view = render(<AgentRuntimePanel projects={[{ sessionId:'session-1', title:'知识问答' }]} />);

    await vi.waitFor(() => expect(signals).toHaveLength(1));
    expect(signals[0].aborted).toBe(false);
    view.unmount();
    expect(signals[0].aborted).toBe(true);
    resolveResponse?.(new Response(JSON.stringify(runtime), { status:200 }));
  });

  it('keeps the previous project snapshot when only that project refresh fails', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce(response(runtime))
      .mockResolvedValueOnce(response([]));
    render(<AgentRuntimePanel projects={[
      { sessionId:'session-1', title:'知识问答' },
      { sessionId:'session-2', title:'空项目' },
    ]} />);

    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText('PM Agent')).toBeTruthy();
    fetchMock
      .mockResolvedValueOnce(response({ detail:{ code:'TEMPORARILY_UNAVAILABLE', message:'retry' } }, 503))
      .mockResolvedValueOnce(response([]));
    await vi.advanceTimersByTimeAsync(2000);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(/知识问答.*最近一次成功快照/)).toBeTruthy();
    expect(screen.getByText('PM Agent')).toBeTruthy();
  });
});
