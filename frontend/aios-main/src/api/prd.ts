import { apiClient } from './client';
import type {
  PrdCommentDto,
  PrdCommentableLinesDto,
  PrdDocumentDto,
  PrdDiffDto,
  PublishReviewAcceptedDto,
  ReviewTaskDto,
  ReviewTaskAcceptedDto,
} from './dto';

export const getLatestPrd = (wi: string, signal?: AbortSignal) =>
  apiClient.request<PrdDocumentDto>(`/prd/${wi}`, { signal });

export const listPrdVersions = (wi: string, signal?: AbortSignal) =>
  apiClient.request<PrdDocumentDto[]>(`/prd/${wi}/versions`, { signal });

export const getCommentableLines = (wi: string, signal?: AbortSignal) =>
  apiClient.request<PrdCommentableLinesDto>(`/prd/${wi}/commentable-lines`, { signal });

export const listPrdComments = (wi: string, signal?: AbortSignal) =>
  apiClient.request<PrdCommentDto[]>(`/prd/${wi}/comments`, { signal });

export const createPrdComment = (
  wi: string,
  input: { line: number; text: string; anchor?: string },
  signal?: AbortSignal,
) => apiClient.request<void>(`/prd/${wi}/comments`, { method: 'POST', body: input, signal });

export const replyPrdComment = (
  wi: string,
  commentId: number,
  text: string,
  signal?: AbortSignal,
) => apiClient.request<void>(`/prd/${wi}/comments/${commentId}/reply`, {
  method: 'POST', body: { text, author_type: 'human' }, signal,
});

export const resolvePrdComment = (
  wi: string,
  commentId: number,
  resolved: boolean,
  signal?: AbortSignal,
) => apiClient.request<void>(`/prd/${wi}/comments/${commentId}/resolve`, {
  method: 'POST', body: { resolved }, signal,
});

export const publishPrdReview = (
  wi: string,
  autoResolveFindings = false,
  signal?: AbortSignal,
) =>
  apiClient.request<PublishReviewAcceptedDto>(`/prd/${wi}/reviews/publish`, {
    method: 'POST', body: { auto_resolve_findings: autoResolveFindings }, signal,
  });

export const getReviewTask = (taskId: string, signal?: AbortSignal) =>
  apiClient.request<ReviewTaskDto>(`/tasks/${taskId}`, { signal });

export const getPrdDiff = (wi: string, signal?: AbortSignal) =>
  apiClient.request<PrdDiffDto>(`/prd/${wi}/diff`, { signal });

export const generatePrdPrototype = (
  wi: string,
  version: number,
  signal?: AbortSignal,
) => apiClient.request<PrdDocumentDto>(`/prd/${wi}/v/${version}/prototype`, {
  method: 'POST', signal, timeoutMs: 30 * 60 * 1000,
});

export const publishDiagramRevision = (
  wi: string,
  diagramId: string,
  input: {
    base_version: number;
    base_commit_sha: string;
    drawio_xml: string;
    change_summary: string;
  },
  signal?: AbortSignal,
) => apiClient.request<ReviewTaskAcceptedDto>(
  `/prd/${wi}/diagrams/${diagramId}/revisions`,
  { method: 'POST', body: input, signal },
);
