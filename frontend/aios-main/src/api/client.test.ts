import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from './client';
import { ApiError } from './errors';
import { autoRepairCommandJob, commandTimeoutMs, createSession } from './sessions';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function fetchUntilAborted() {
  return vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => {
      reject(new DOMException('aborted', 'AbortError'));
    }, { once: true });
  }));
}

describe('ApiClient', () => {
  it('parses JSON success and supports 204 responses', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient('http://backend.test');

    await expect(client.request('/healthz')).resolves.toEqual({ status: 'ok' });
    await expect(client.request('/comment', { method: 'POST' })).resolves.toBeUndefined();
  });

  it('normalizes the backend stable error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: { code: 'STALE_STATE', message: 'refresh', errors: [] } }),
          { status: 409, statusText: 'Conflict' },
        ),
      ),
    );
    const client = new ApiClient('http://backend.test');

    const error = await client.request('/sessions/1/commands').catch((reason) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: 'STALE_STATE', retryable: false });
  });

  it('distinguishes an internal timeout from a caller cancellation', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', fetchUntilAborted());
    const client = new ApiClient('http://backend.test');

    const timedOut = client.request('/slow', { timeoutMs: 10 }).catch((reason) => reason);
    await vi.advanceTimersByTimeAsync(10);
    await expect(timedOut).resolves.toMatchObject({
      code: 'REQUEST_TIMEOUT',
      retryable: true,
    });

    const caller = new AbortController();
    const cancelled = client.request('/cancelled', { signal: caller.signal }).catch((reason) => reason);
    caller.abort();
    await expect(cancelled).resolves.toMatchObject({
      code: 'REQUEST_ABORTED',
      retryable: false,
    });
  });

  it('allows the configured backend Agent calls to finish before timing out the command', () => {
    expect(commandTimeoutMs('create_spec')).toBe(4_150_000);
    expect(commandTimeoutMs('revise')).toBe(4_150_000);
    expect(commandTimeoutMs('restore_spec_version')).toBe(4_150_000);
    expect(commandTimeoutMs('convert_to_work_item')).toBe(130_000);
    expect(commandTimeoutMs('publish_review')).toBe(4_150_000);
    expect(commandTimeoutMs('message')).toBe(2_075_000);
    expect(commandTimeoutMs('skip_clarification')).toBe(130_000);
  });

  it('times out createSession at its single-Agent budget', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', fetchUntilAborted());
    const pending = createSession('request-1', {
      motivation: 'm', final_objective: 'o', known_scope: [], exclusions: [],
      reference_materials: [], expected_deliverables: [], time_constraints: 'none',
      staffing_constraints: 'none', final_approver: 'owner-1',
      project_manager_ids: ['owner-1'], root_owner_ids: ['owner-1'],
    }).catch((reason) => reason);
    let settled = false;
    void pending.finally(() => { settled = true; });

    await vi.advanceTimersByTimeAsync(2_074_999);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toMatchObject({
      code: 'REQUEST_TIMEOUT',
      retryable: true,
    });
  });

  it('never injects a browser actor into session creation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session_id: 's1', project_id: 'p1', phase: 'INTAKE', state_version: 1,
          current_spec_version_id: null, current_spec_status: null, legal_actions: [],
          next_action: 'wait', outstanding_questions: [], review_findings: [],
        }),
        { status: 201 },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await createSession('request-1', {
      motivation: 'm', final_objective: 'o', known_scope: [], exclusions: [],
      reference_materials: [], expected_deliverables: [], time_constraints: 'none',
      staffing_constraints: 'none', final_approver: 'owner-1',
      project_manager_ids: ['owner-1'], root_owner_ids: ['owner-1'],
    });

    const request = fetchMock.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(request.body as string) as Record<string, unknown>;
    expect(body).not.toHaveProperty('actor_id');
  });

  it('sends auto-repair input as one JSON object', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          command_id: 'repair-1',
          status_url: '/sessions/session-1/commands/repair-1',
          events_url: '/sessions/session-1/commands/repair-1/events',
        }),
        { status: 202 },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await autoRepairCommandJob('session-1', 'failed-1', 'owner-1');

    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(request.body as string)).toEqual({ actor_id: 'owner-1' });
  });
});
