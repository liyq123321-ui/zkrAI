import { ApiError } from './errors';
import { commandJobEventsUrl, getCommandJob } from './sessions';
import type {
  CommandJobAcceptedDto,
  CommandJobReadDto,
  CommandResultDto,
} from './dto';

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
    options.onStatus?.(snapshot);
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
        sseUnavailable = false;
        reconcile(JSON.parse((event as MessageEvent<string>).data));
      } catch {
        // A malformed frame is ignored; the durable poll remains authoritative.
      }
    });
    source.onerror = () => { sseUnavailable = true; };
  } catch {
    // Polling remains active and will report a transport problem if it also fails.
    sseUnavailable = true;
  }

  return { completion, close };
}
