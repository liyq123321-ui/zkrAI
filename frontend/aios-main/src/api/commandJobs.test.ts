import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  CommandJobAcceptedDto,
  CommandJobReadDto,
  SessionStateDto,
} from './dto';
import { observeCommandJob } from './commandJobs';

const sessionState: SessionStateDto = {
  session_id: 'session-1',
  project_id: 'project-1',
  phase: 'PLANNING',
  state_version: 3,
  current_spec_version_id: 'spec-1',
  current_spec_status: 'approved',
  legal_actions: ['convert_to_work_item'],
  next_action: 'review work items',
  outstanding_questions: [],
  review_findings: [],
};

const accepted: CommandJobAcceptedDto = {
  command_id: 'decompose-1',
  status: 'pending',
  status_url: '/sessions/session-1/commands/decompose-1',
  events_url: '/sessions/session-1/commands/decompose-1/events',
};

const processing: CommandJobReadDto = {
  command_id: 'decompose-1',
  status: 'processing',
  status_version: 2,
  result: null,
  error: null,
  created_at: '2026-09-05T00:00:00Z',
  started_at: '2026-09-05T00:00:01Z',
  completed_at: null,
};

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, Array<(event: MessageEvent) => void>>();
  close = vi.fn();
  onerror: ((event: Event) => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    const current = this.listeners.get(type) ?? [];
    current.push(listener as (event: MessageEvent) => void);
    this.listeners.set(type, current);
  }

  emit(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  emitRaw(type: string, data: string) {
    const event = new MessageEvent(type, { data });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function succeededJob(): CommandJobReadDto {
  return {
    ...processing,
    status: 'succeeded',
    status_version: 3,
    result: {
      command_id: 'decompose-1',
      state: sessionState,
      created_resource_ids: ['work-item-1'],
    },
    completed_at: '2026-09-05T00:00:10Z',
  };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('observeCommandJob', () => {
  it('polls every 5000 ms without overlapping requests when SSE is quiet', async () => {
    vi.useFakeTimers();
    const poll = vi.fn<() => Promise<CommandJobReadDto>>();
    let release!: (value: CommandJobReadDto) => void;
    poll.mockImplementation(() => new Promise((resolve) => { release = resolve; }));

    const observer = observeCommandJob('session-1', accepted, { poll });
    await vi.advanceTimersByTimeAsync(5_000);
    expect(poll).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(poll).toHaveBeenCalledTimes(1);

    release(processing);
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(4_999);
    expect(poll).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(poll).toHaveBeenCalledTimes(2);
    observer.close();
  });

  it('keeps polling after SSE error and resolves once on terminal status', async () => {
    vi.useFakeTimers();
    const succeeded = succeededJob();
    const poll = vi.fn().mockResolvedValue(succeeded);
    const observer = observeCommandJob('session-1', accepted, { poll });
    FakeEventSource.instances[0].onerror?.(new Event('error'));
    await vi.advanceTimersByTimeAsync(5_000);

    await expect(observer.completion).resolves.toEqual(succeeded.result);
    expect(FakeEventSource.instances[0].close).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it('ignores an older poll after SSE has delivered a newer version', async () => {
    vi.useFakeTimers();
    const poll = vi.fn().mockResolvedValue(processing);
    const onStatus = vi.fn();
    const observer = observeCommandJob('session-1', accepted, { poll, onStatus });
    const succeeded = succeededJob();
    FakeEventSource.instances[0].emit('command.status', succeeded);

    await expect(observer.completion).resolves.toEqual(succeeded.result);
    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenLastCalledWith(succeeded);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(poll).not.toHaveBeenCalled();
  });

  it('reports connectivity trouble only when SSE and polling are both unavailable', async () => {
    vi.useFakeTimers();
    const transportError = vi.fn();
    const poll = vi.fn().mockRejectedValue(new Error('offline'));
    const observer = observeCommandJob('session-1', accepted, {
      poll,
      onTransportError: transportError,
    });
    FakeEventSource.instances[0].onerror?.(new Event('error'));
    await vi.advanceTimersByTimeAsync(5_000);

    expect(transportError).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances[0].close).not.toHaveBeenCalled();
    observer.close();
  });

  it('ignores malformed SSE frames and lets the durable poll finish the job', async () => {
    vi.useFakeTimers();
    const succeeded = succeededJob();
    const poll = vi.fn().mockResolvedValue(succeeded);
    const observer = observeCommandJob('session-1', accepted, { poll });

    FakeEventSource.instances[0].emitRaw('command.status', '{not json');
    await vi.advanceTimersByTimeAsync(5_000);

    await expect(observer.completion).resolves.toEqual(succeeded.result);
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it('rejects with the durable terminal failure and closes resources', async () => {
    const failed: CommandJobReadDto = {
      ...processing,
      status: 'failed',
      status_version: 3,
      error: { code: 'WORKFLOW_ERROR', message: 'decomposition failed' },
      completed_at: '2026-09-05T00:00:10Z',
    };
    const observer = observeCommandJob('session-1', accepted, {
      poll: vi.fn().mockResolvedValue(processing),
    });

    FakeEventSource.instances[0].emit('command.status', failed);

    await expect(observer.completion).rejects.toMatchObject({
      code: 'WORKFLOW_ERROR',
      message: 'decomposition failed',
    });
    expect(FakeEventSource.instances[0].close).toHaveBeenCalledTimes(1);
  });

  it('cleans up idempotently when the caller closes a pending observer', () => {
    const observer = observeCommandJob('session-1', accepted, {
      poll: vi.fn().mockResolvedValue(processing),
    });

    observer.close();
    observer.close();

    expect(FakeEventSource.instances[0].close).toHaveBeenCalledTimes(1);
  });
});
