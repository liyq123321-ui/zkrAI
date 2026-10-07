import { apiClient } from './client';
import { appConfig } from './config';
import { getToken } from '../auth/session';
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
  ImportedPrdSourceDto,
  ProjectBriefDto,
  SessionStateDto,
  SessionSummaryDto,
  SpecVersionDto,
  WorkItemDto,
  SemanticaStatusDto,
  SemanticaDecisionsDto,
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

export function importPrdSource(
  filename: string,
  contentBase64: string,
  signal?: AbortSignal,
) {
  return apiClient.request<ImportedPrdSourceDto>('/sessions/import-prd', {
    method: 'POST',
    body: { filename, content_base64: contentBase64 },
    signal,
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

export interface WorkspaceChatMessageDto {
  id: string;
  role: 'user' | 'agent';
  body: string;
  detail?: string | null;
  image_ids?: string[];
  createdAt: string;
}

export interface ChatImageAnchorDto { anchor_text: string; heading: string; visual_context: string }

export const uploadWorkspaceChatImage = (sessionId: string, actorId: string, mimeType: string, contentBase64: string) =>
  apiClient.request<{ id: string }>(`/sessions/${encodeURIComponent(sessionId)}/chat-images`, {
    method: 'POST', body: { actor_id: actorId, mime_type: mimeType, content_base64: contentBase64 },
    timeoutMs: 45_000,
  });

export const locateWorkspaceChatImage = (sessionId: string, imageId: string, actorId: string) =>
  apiClient.request<ChatImageAnchorDto>(`/sessions/${encodeURIComponent(sessionId)}/chat-images/${encodeURIComponent(imageId)}/locate?actor_id=${encodeURIComponent(actorId)}`, {
    method: 'POST', timeoutMs: 180_000,
  });

export async function loadWorkspaceChatImage(sessionId: string, imageId: string, actorId: string): Promise<string> {
  const token = getToken();
  const response = await fetch(`${appConfig.apiBaseUrl}/sessions/${encodeURIComponent(sessionId)}/chat-images/${encodeURIComponent(imageId)}?actor_id=${encodeURIComponent(actorId)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new Error('无法读取对话图片。');
  return URL.createObjectURL(await response.blob());
}

export type PrdPresentationAudience = 'team' | 'external';

export async function generateAndDownloadPrdPresentation(
  sessionId: string, actorId: string, audience: PrdPresentationAudience, expectedSpecId?: string | null,
): Promise<void> {
  const path = `/sessions/${encodeURIComponent(sessionId)}/presentations/${audience}`;
  const query = `?actor_id=${encodeURIComponent(actorId)}${expectedSpecId ? `&expected_spec_id=${encodeURIComponent(expectedSpecId)}` : ''}`;
  await apiClient.request(path + query, {
    method: 'POST', timeoutMs: 30 * 60 * 1000,
  });
  const token = getToken();
  const response = await fetch(`${appConfig.apiBaseUrl}${path}/file?actor_id=${encodeURIComponent(actorId)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new Error('PPT 已生成，但下载失败，请重试。');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = `prd-${audience}.pptx`;
  document.body.append(link);
  link.click();
  link.remove();
  globalThis.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export const listWorkspaceChatMessages = (sessionId: string, actorId: string, signal?: AbortSignal) =>
  apiClient.request<WorkspaceChatMessageDto[]>(`/sessions/${encodeURIComponent(sessionId)}/chat-messages?actor_id=${encodeURIComponent(actorId)}`, { signal });

export const saveWorkspaceChatMessage = (sessionId: string, actorId: string, message: WorkspaceChatMessageDto) =>
  apiClient.request<WorkspaceChatMessageDto>(`/sessions/${encodeURIComponent(sessionId)}/chat-messages`, {
    method: 'POST',
    body: { ...message, actor_id: actorId },
  });

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

export const getSemanticaStatus = (signal?: AbortSignal) =>
  apiClient.request<SemanticaStatusDto>('/semantica/status', { signal });

export const listSemanticaDecisions = (
  projectId: string,
  agentSessionId?: string,
  signal?: AbortSignal,
) => apiClient.request<SemanticaDecisionsDto>(
  `/semantica/decisions?project_id=${encodeURIComponent(projectId)}${
    agentSessionId ? `&agent_session_id=${encodeURIComponent(agentSessionId)}` : ''
  }`,
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
  { method: 'POST', body: { actor_id: actorId } },
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
