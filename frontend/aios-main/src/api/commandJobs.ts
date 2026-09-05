import { ApiError } from './errors';
import { commandJobEventsUrl, getCommandJob } from './sessions';
import type {
  CommandJobAcceptedDto,
  CommandJobReadDto,
  CommandResultDto,
} from './dto';

const commandJobStatuses = new Set(['pending', 'processing', 'succeeded', 'failed']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isCommandResult(value: unknown, commandId: string): value is CommandResultDto {
  return isRecord(value)
    && value.command_id === commandId
    && isRecord(value.state)
    && isStringArray(value.created_resource_ids);
}

function isErrorDetail(value: unknown): value is { code: string; message: string } {
  return isRecord(value)
    && typeof value.code === 'string'
    && typeof value.message === 'string';
}

function isCommandJobSnapshot(value: unknown, commandId: string): value is CommandJobReadDto {
  if (!isRecord(value)
    || value.command_id !== commandId
    || typeof value.status !== 'string'
    || !commandJobStatuses.has(value.status)
    || typeof value.status_version !== 'number'
    || !Number.isInteger(value.status_version)
    || value.status_version < 1
    || typeof value.created_at !== 'string'
    || (value.started_at !== null && typeof value.started_at !== 'string')
    || (value.completed_at !== null && typeof value.completed_at !== 'string')) {
    return false;
  }
  if (value.status === 'succeeded') {
    return isCommandResult(value.result, commandId) && value.error === null;
  }
  if (value.status === 'failed') {
    return value.result === null && (value.error === null || isErrorDetail(value.error));
  }
  return value.result === null && value.error === null;
}

type ObserverOptions = {
  poll?: () => Promise<CommandJobReadDto>;
  eventSourceFactory?: (url: string) => EventSource;
  pollIntervalMs?: number;
  onStatus?: (status: CommandJobReadDto) => void;
  onTransportError?: (error: unknown) => void;
};

export function observeCommandJob(
  sessionId: string,
  accepted: CommandJobAcceptedDto,
  options: ObserverOptions = {},
): { completion: Promise<CommandResultDto>; close: () => void } {
  const controller = new AbortController();
  const pollIntervalMs = options.pollIntervalMs ?? 5_000;
  const poll = options.poll ?? (() =>
    getCommandJob(sessionId, accepted.command_id, controller.signal));
  const makeSource = options.eventSourceFactory ?? ((url: string) => new EventSource(url));
  let timer: ReturnType<typeof setTimeout> | undefined;
  let source: EventSource | undefined;
  let closed = false;
  let sseUnavailable = false;
  let lastVersion = 0;
  let resolveCompletion!: (result: CommandResultDto) => void;
  let rejectCompletion!: (error: unknown) => void;
  const completion = new Promise<CommandResultDto>((resolve, reject) => {
    resolveCompletion = resolve;
    rejectCompletion = reject;
  });

  const close = () => {
    if (closed) return;
    closed = true;
    if (timer !== undefined) clearTimeout(timer);
    controller.abort();
    source?.close();
  };

  const reconcile = (snapshot: CommandJobReadDto) => {
    if (closed || snapshot.status_version <= lastVersion) return;
    lastVersion = snapshot.status_version;
    try {
      options.onStatus?.(snapshot);
    } catch {
      // Consumer rendering callbacks cannot interrupt durable completion handling.
    }
    if (snapshot.status === 'succeeded' && snapshot.result) {
      close();
      resolveCompletion(snapshot.result);
    } else if (snapshot.status === 'failed') {
      const error = snapshot.error ?? {
        code: 'WORKFLOW_ERROR', message: '后台拆分任务失败。',
      };
      close();
      rejectCompletion(new ApiError({
        status: 0,
        code: error.code,
        message: error.message,
        retryable: true,
      }));
    }
  };

  const schedulePoll = () => {
    timer = setTimeout(async () => {
      try {
        reconcile(await poll());
      } catch (error) {
        if (controller.signal.aborted) return;
        if (sseUnavailable) options.onTransportError?.(error);
      } finally {
        if (!closed) schedulePoll();
      }
    }, pollIntervalMs);
  };

  schedulePoll();
  try {
    source = makeSource(commandJobEventsUrl(accepted.events_url));
    source.addEventListener('command.status', (event) => {
      try {
        const snapshot: unknown = JSON.parse((event as MessageEvent<string>).data);
        if (!isCommandJobSnapshot(snapshot, accepted.command_id)) return;
        sseUnavailable = false;
        reconcile(snapshot);
      } catch {
        // A malformed frame is ignored; the durable poll remains authoritative.
      }
    });
    source.addEventListener('open', () => { sseUnavailable = false; });
    source.onerror = () => { sseUnavailable = true; };
  } catch {
    // Polling remains active and will report a transport problem if it also fails.
    sseUnavailable = true;
  }

  return { completion, close };
}
