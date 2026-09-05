import { ApiError } from './errors';
import { commandJobEventsUrl, getCommandJob } from './sessions';
import type {
  CommandJobAcceptedDto,
  CommandJobReadDto,
  CommandResultDto,
} from './dto';

const commandJobStatuses = new Set(['pending', 'processing', 'succeeded', 'failed']);
const commandActions = new Set([
  'message',
  'skip_clarification',
  'create_spec',
  'revise',
  'approve',
  'reject',
  'rework',
  'publish_review',
  'convert_to_work_item',
  'restore_spec_version',
  'start_task',
  'complete_task',
  'fail_task',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isClarificationQuestion(value: unknown): boolean {
  return isRecord(value)
    && typeof value.question_id === 'string'
    && typeof value.question === 'string'
    && typeof value.reason === 'string'
    && isStringArray(value.affected_areas)
    && typeof value.blocking === 'boolean';
}

function isSessionState(value: unknown, sessionId: string): boolean {
  return isRecord(value)
    && value.session_id === sessionId
    && typeof value.project_id === 'string'
    && typeof value.phase === 'string'
    && typeof value.state_version === 'number'
    && Number.isInteger(value.state_version)
    && value.state_version >= 0
    && (value.current_spec_version_id === null
      || typeof value.current_spec_version_id === 'string')
    && (value.current_spec_status === null
      || typeof value.current_spec_status === 'string')
    && Array.isArray(value.legal_actions)
    && value.legal_actions.every((action) =>
      typeof action === 'string' && commandActions.has(action))
    && typeof value.next_action === 'string'
    && Array.isArray(value.outstanding_questions)
    && value.outstanding_questions.every(isClarificationQuestion)
    && Array.isArray(value.review_findings)
    && value.review_findings.every(isRecord);
}

function isCommandResult(
  value: unknown,
  commandId: string,
  sessionId: string,
): value is CommandResultDto {
  return isRecord(value)
    && value.command_id === commandId
    && isSessionState(value.state, sessionId)
    && isStringArray(value.created_resource_ids);
}

function isErrorDetail(value: unknown): value is { code: string; message: string } {
  return isRecord(value)
    && typeof value.code === 'string'
    && typeof value.message === 'string';
}

function isOptionalNullableString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === 'string';
}

function isCommandJobSnapshot(
  value: unknown,
  commandId: string,
  sessionId: string,
): value is CommandJobReadDto {
  if (!isRecord(value)
    || value.command_id !== commandId
    || typeof value.status !== 'string'
    || !commandJobStatuses.has(value.status)
    || typeof value.status_version !== 'number'
    || !Number.isInteger(value.status_version)
    || value.status_version < 1
    || typeof value.created_at !== 'string'
    || !isOptionalNullableString(value.progress_stage)
    || !isOptionalNullableString(value.progress_message)
    || !isOptionalNullableString(value.last_activity_at)
    || (value.started_at !== null && typeof value.started_at !== 'string')
    || (value.completed_at !== null && typeof value.completed_at !== 'string')) {
    return false;
  }
  if (value.status === 'succeeded') {
    return isCommandResult(value.result, commandId, sessionId) && value.error === null;
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
  pollImmediately?: boolean;
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
        status: error.code === 'STALE_STATE' ? 409 : 0,
        code: error.code,
        message: error.message,
        retryable: true,
      }));
    }
  };

  const pollOnce = async () => {
    try {
      const snapshot: unknown = await poll();
      if (isCommandJobSnapshot(snapshot, accepted.command_id, sessionId)) {
        reconcile(snapshot);
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      if (sseUnavailable) options.onTransportError?.(error);
    } finally {
      if (!closed) schedulePoll();
    }
  };

  const schedulePoll = () => {
    timer = setTimeout(() => { void pollOnce(); }, pollIntervalMs);
  };

  if (options.pollImmediately) void pollOnce();
  else schedulePoll();
  try {
    source = makeSource(commandJobEventsUrl(accepted.events_url));
    source.addEventListener('command.status', (event) => {
      try {
        const snapshot: unknown = JSON.parse((event as MessageEvent<string>).data);
        if (!isCommandJobSnapshot(snapshot, accepted.command_id, sessionId)) return;
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
