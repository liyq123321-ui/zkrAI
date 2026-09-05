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
  it('can read durable status immediately, then waits 5000 ms after that request completes', async () => {
    vi.useFakeTimers();
    let release!: (value: CommandJobReadDto) => void;
    const poll = vi.fn(() => new Promise<CommandJobReadDto>((resolve) => { release = resolve; }));

    const observer = observeCommandJob('session-1', accepted, {
      poll,
      pollImmediately: true,
    });
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

  it('ignores an older in-flight poll after SSE has delivered a newer version', async () => {
    vi.useFakeTimers();
    let release!: (value: CommandJobReadDto) => void;
    const poll = vi.fn(() => new Promise<CommandJobReadDto>((resolve) => { release = resolve; }));
    const onStatus = vi.fn();
    const observer = observeCommandJob('session-1', accepted, { poll, onStatus });
    await vi.advanceTimersByTimeAsync(5_000);
    expect(poll).toHaveBeenCalledTimes(1);

    const newer: CommandJobReadDto = {
      ...processing,
      status_version: 3,
    };
    FakeEventSource.instances[0].emit('command.status', newer);
    release(processing);
    await Promise.resolve();

    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenLastCalledWith(newer);
    observer.close();
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

  it('restores SSE availability on open before a transient poll failure', async () => {
    vi.useFakeTimers();
    const transportError = vi.fn();
    const observer = observeCommandJob('session-1', accepted, {
      poll: vi.fn().mockRejectedValue(new Error('offline')),
      onTransportError: transportError,
    });

    FakeEventSource.instances[0].onerror?.(new Event('error'));
    FakeEventSource.instances[0].emit('open', {});
    await vi.advanceTimersByTimeAsync(5_000);

    expect(transportError).not.toHaveBeenCalled();
    observer.close();
  });

  it('discards invalid JSON snapshots without advancing the version', () => {
    const onStatus = vi.fn();
    const observer = observeCommandJob('session-1', accepted, {
      poll: vi.fn().mockResolvedValue(processing),
      onStatus,
    });
    const source = FakeEventSource.instances[0];

    source.emit('command.status', { ...processing, command_id: 'other-command', status_version: 8 });
    source.emit('command.status', { ...processing, status: 'unknown', status_version: 9 });
    source.emit('command.status', { ...processing, status_version: 0 });
    source.emit('command.status', {
      ...succeededJob(),
      status_version: 10,
      result: { command_id: 'decompose-1', state: null, created_resource_ids: [] },
    });
    source.emit('command.status', {
      ...processing,
      status: 'failed',
      status_version: 11,
      error: { code: 4, message: 'not a string' },
    });
    source.emit('command.status', {
      ...processing,
      status_version: 12,
      progress_message: 4,
    });
    source.emit('command.status', processing);

    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenLastCalledWith(processing);
    observer.close();
  });

  it('does not let invalid poll snapshots suppress a later valid SSE terminal status', async () => {
    vi.useFakeTimers();
    const invalidPolls: CommandJobReadDto[] = [
      { ...processing, command_id: 'other-command', status_version: 99 },
      { ...processing, status: 'unknown', status_version: 100 },
      { ...processing, status_version: 101.5 },
      { ...succeededJob(), status_version: 102, result: null },
    ] as unknown as CommandJobReadDto[];
    const poll = vi.fn(() => Promise.resolve(invalidPolls.shift() ?? processing));
    const onStatus = vi.fn();
    const observer = observeCommandJob('session-1', accepted, { poll, onStatus });

    for (let index = 0; index < 4; index += 1) {
      await vi.advanceTimersByTimeAsync(5_000);
    }
    expect(poll).toHaveBeenCalledTimes(4);
    expect(onStatus).not.toHaveBeenCalled();

    const succeeded = succeededJob();
    FakeEventSource.instances[0].emit('command.status', succeeded);

    await expect(observer.completion).resolves.toEqual(succeeded.result);
    expect(onStatus).toHaveBeenLastCalledWith(succeeded);
  });

  it('rejects a terminal result whose Session state is only a partial object', async () => {
    vi.useFakeTimers();
    const partial = {
      ...succeededJob(),
      status_version: 99,
      result: {
        command_id: 'decompose-1',
        state: { session_id: 'session-1' },
        created_resource_ids: ['work-item-1'],
      },
    } as unknown as CommandJobReadDto;
    const poll = vi.fn().mockResolvedValue(partial);
    const onStatus = vi.fn();
    const observer = observeCommandJob('session-1', accepted, {
      poll,
      onStatus,
      pollImmediately: true,
    });
    await Promise.resolve();

    expect(onStatus).not.toHaveBeenCalled();
    const succeeded = succeededJob();
    FakeEventSource.instances[0].emit('command.status', succeeded);

    await expect(observer.completion).resolves.toEqual(succeeded.result);
    expect(onStatus).toHaveBeenCalledWith(succeeded);
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

  it('preserves STALE_STATE as an actionable durable conflict', async () => {
    const failed: CommandJobReadDto = {
      ...processing,
      status: 'failed',
      status_version: 3,
      error: {
        code: 'STALE_STATE',
        message: 'The workflow state changed; retry with current state.',
      },
      completed_at: '2026-09-05T00:00:10Z',
    };
    const observer = observeCommandJob('session-1', accepted, {
      poll: vi.fn().mockResolvedValue(processing),
    });

    FakeEventSource.instances[0].emit('command.status', failed);

    await expect(observer.completion).rejects.toMatchObject({
      status: 409,
      code: 'STALE_STATE',
      retryable: true,
    });
  });

  it('settles terminal completion even when onStatus throws', async () => {
    const observer = observeCommandJob('session-1', accepted, {
      poll: vi.fn().mockResolvedValue(processing),
      onStatus: () => { throw new Error('render failed'); },
    });
    let completed = false;
    void observer.completion.then(() => { completed = true; });

    FakeEventSource.instances[0].emit('command.status', succeededJob());
    await Promise.resolve();

    expect(completed).toBe(true);
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
