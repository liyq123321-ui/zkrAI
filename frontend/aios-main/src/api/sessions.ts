import { apiClient } from './client';
import type {
  AgentSpecDto,
  AuditEventDto,
  CommandAction,
  CommandResultDto,
  ProjectBriefDto,
  SessionStateDto,
  SessionSummaryDto,
  SpecVersionDto,
  WorkItemDto,
} from './dto';

const STANDARD_COMMAND_TIMEOUT_MS = 130_000;
const SINGLE_AGENT_COMMAND_TIMEOUT_MS = 975_000;
const DOUBLE_AGENT_COMMAND_TIMEOUT_MS = 1_950_000;

const DOUBLE_AGENT_ACTIONS = new Set<CommandAction>([
  'create_spec',
  'revise',
  'restore_spec_version',
  'convert_to_work_item',
  'publish_review',
]);

export function commandTimeoutMs(action: CommandAction): number {
  if (DOUBLE_AGENT_ACTIONS.has(action)) return DOUBLE_AGENT_COMMAND_TIMEOUT_MS;
  if (action === 'message') return SINGLE_AGENT_COMMAND_TIMEOUT_MS;
  return STANDARD_COMMAND_TIMEOUT_MS;
}

export function createSession(requestId: string, brief: ProjectBriefDto, signal?: AbortSignal) {
  return apiClient.request<SessionStateDto>('/sessions', {
    method: 'POST',
    body: { request_id: requestId, brief },
    signal,
    timeoutMs: SINGLE_AGENT_COMMAND_TIMEOUT_MS,
  });
}

export function getSessionState(sessionId: string, signal?: AbortSignal) {
  return apiClient.request<SessionStateDto>(`/sessions/${sessionId}/state`, { signal });
}

export const listSessions = (signal?: AbortSignal) =>
  apiClient.request<SessionSummaryDto[]>('/sessions', { signal });

export function executeCommand(
  sessionId: string,
  input: {
    commandId: string;
    action: CommandAction;
    expectedStateVersion: number;
    message?: string;
    payload?: Record<string, unknown>;
  },
  signal?: AbortSignal,
) {
  return apiClient.request<CommandResultDto>(`/sessions/${sessionId}/commands`, {
    method: 'POST',
    body: {
      command_id: input.commandId,
      action: input.action,
      expected_state_version: input.expectedStateVersion,
      message: input.message,
      payload: input.payload ?? {},
    },
    signal,
    timeoutMs: commandTimeoutMs(input.action),
  });
}

export const listSpecs = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<SpecVersionDto[]>(`/sessions/${sessionId}/specs`, { signal });

export const getSpec = (sessionId: string, version: string | number, signal?: AbortSignal) =>
  apiClient.request<SpecVersionDto>(`/sessions/${sessionId}/specs/${version}`, { signal });

export const listWorkItems = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<WorkItemDto[]>(`/sessions/${sessionId}/work-items`, { signal });

export const getWorkItem = (sessionId: string, workItemId: string, signal?: AbortSignal) =>
  apiClient.request<WorkItemDto>(`/sessions/${sessionId}/work-items/${workItemId}`, { signal });

export const listAgentSpecs = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<AgentSpecDto[]>(`/sessions/${sessionId}/agent-specs`, { signal });

export const getAgentSpec = (sessionId: string, agentSpecId: string, signal?: AbortSignal) =>
  apiClient.request<AgentSpecDto>(`/sessions/${sessionId}/agent-specs/${agentSpecId}`, { signal });

export const listEvents = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<AuditEventDto[]>(`/sessions/${sessionId}/events`, { signal });
