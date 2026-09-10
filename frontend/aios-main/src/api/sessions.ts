import { apiClient } from './client';
import { appConfig } from './config';
import type {
  AgentBackendName,
  AgentBackendOptionDto,
  AgentBackendSelectionDto,
  AgentRuntimeEventDto,
  AgentRuntimeDto,
  AgentSpecDto,
  AuditEventDto,
  CommandAction,
  CommandJobAcceptedDto,
  CommandJobReadDto,
  CommandResultDto,
  CommandSubmissionDto,
  LifecycleModel,
  LifecycleRouteDecisionDto,
  ProjectBriefDto,
  SessionStateDto,
  SessionSummaryDto,
  SpecVersionDto,
  WorkItemDto,
} from './dto';

const STANDARD_COMMAND_TIMEOUT_MS = 130_000;
const SINGLE_AGENT_COMMAND_TIMEOUT_MS = 2_075_000;
const DOUBLE_AGENT_COMMAND_TIMEOUT_MS = 4_150_000;

const DOUBLE_AGENT_ACTIONS = new Set<CommandAction>([
  'create_spec',
  'revise',
  'restore_spec_version',
  'publish_review',
]);

export function commandTimeoutMs(action: CommandAction): number {
  if (DOUBLE_AGENT_ACTIONS.has(action)) return DOUBLE_AGENT_COMMAND_TIMEOUT_MS;
  if (action === 'message') return SINGLE_AGENT_COMMAND_TIMEOUT_MS;
  return STANDARD_COMMAND_TIMEOUT_MS;
}

export function createSession(
  requestId: string,
  brief: ProjectBriefDto,
  signal?: AbortSignal,
  agentBackend: AgentBackendName = 'codex',
) {
  return apiClient.request<SessionStateDto>('/sessions', {
    method: 'POST',
    body: { request_id: requestId, agent_backend: agentBackend, brief },
    signal,
    timeoutMs: SINGLE_AGENT_COMMAND_TIMEOUT_MS,
  });
}

export const listAgentBackends = (signal?: AbortSignal) =>
  apiClient.request<AgentBackendOptionDto[]>('/sessions/agent-backends', { signal });

export const getAgentBackend = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<AgentBackendSelectionDto>(`/sessions/${sessionId}/agent-backend`, { signal });

export const selectAgentBackend = (
  sessionId: string,
  provider: AgentBackendName,
  actorId: string,
  signal?: AbortSignal,
) => apiClient.request<AgentBackendSelectionDto>(`/sessions/${sessionId}/agent-backend`, {
  method: 'PUT',
  body: { provider, actor_id: actorId },
  signal,
});

export const getLifecycleRoutes = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<LifecycleRouteDecisionDto | null>(`/sessions/${sessionId}/sdlc/routes`, { signal });

export const recommendLifecycleRoutes = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<LifecycleRouteDecisionDto>(`/sessions/${sessionId}/sdlc/routes/recommend`, {
    method: 'POST',
    signal,
    timeoutMs: SINGLE_AGENT_COMMAND_TIMEOUT_MS,
  });

export const selectLifecycleRoute = (sessionId: string, model: LifecycleModel, signal?: AbortSignal) =>
  apiClient.request<LifecycleRouteDecisionDto>(`/sessions/${sessionId}/sdlc/routes/select`, {
    method: 'POST',
    body: { model },
    signal,
  });

export function getSessionState(sessionId: string, signal?: AbortSignal) {
  return apiClient.request<SessionStateDto>(`/sessions/${sessionId}/state`, { signal });
}

export const listSessions = (signal?: AbortSignal) =>
  apiClient.request<SessionSummaryDto[]>('/sessions', { signal });

export const listAgentRuntime = (sessionId: string, signal?: AbortSignal) =>
  apiClient.request<AgentRuntimeDto[]>(`/sessions/${sessionId}/agents/runtime`, { signal });

export const listAgentRuntimeEvents = (
  sessionId: string,
  agentSessionId: string,
  signal?: AbortSignal,
) => apiClient.request<AgentRuntimeEventDto[]>(
  `/sessions/${sessionId}/agents/${agentSessionId}/runtime-events`,
  { signal },
);

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
  return apiClient.request<CommandSubmissionDto>(`/sessions/${sessionId}/commands`, {
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

export function isCommandJobAccepted(
  value: CommandSubmissionDto,
): value is CommandJobAcceptedDto {
  return 'status_url' in value && 'events_url' in value;
}

export const getCommandJob = (
  sessionId: string,
  commandId: string,
  signal?: AbortSignal,
) => apiClient.request<CommandJobReadDto>(
  `/sessions/${sessionId}/commands/${commandId}`,
  { signal },
);

export const autoRepairCommandJob = (
  sessionId: string,
  commandId: string,
  actorId: string,
) => apiClient.request<CommandJobAcceptedDto>(
  `/sessions/${sessionId}/commands/${commandId}/auto-repair`,
  { method: 'POST', body: JSON.stringify({ actor_id: actorId }) },
);

export const commandJobEventsUrl = (eventsUrl: string) =>
  `${appConfig.apiBaseUrl}${eventsUrl}`;

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
