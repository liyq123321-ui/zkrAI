import { apiClient } from '../client';
import { appConfig } from '../config';
import type { Snapshot } from './types';
export const documentPath = (id: string) => `/prd-documents/${encodeURIComponent(id)}`;
export const openDocument = (sessionId: string, autoGenerate = false, deferOutline = false) => apiClient.request<Snapshot>('/prd-documents', { method: 'POST', body: { session_id: sessionId, auto_generate: autoGenerate, defer_outline: deferOutline } });
export const getDocument = (id: string) => apiClient.request<Snapshot>(documentPath(id));
export interface DocumentVersion { id: string; version: number; source: string; created_at: string }
export interface DocumentVersionDetail extends DocumentVersion { snapshot: Pick<Snapshot, 'document' | 'blocks'> }
export const getDocumentVersions = (id: string) => apiClient.request<DocumentVersion[]>(documentPath(id) + '/versions');
export const getDocumentVersion = (id: string, version: number) => apiClient.request<DocumentVersionDetail>(documentPath(id) + `/versions/${version}`);
export const mutate = <T = Snapshot>(id: string, path: string, body: object, method = 'POST') =>
  apiClient.request<T>(documentPath(id) + path, { method, body });
/** One Agent call decides the hierarchy an imported PRD arrived without. */
export const structureSource = (id: string, expectedRevision: number) =>
  apiClient.request<Snapshot & { organized?: { headings: number; summary: string } }>(
    documentPath(id) + '/structure-source',
    { method: 'POST', body: { expected_revision: expectedRevision }, timeoutMs: 300_000 },
  );

export interface DraftPrototypeStatus {
  document_id: string;
  revision: number;
  status: 'missing' | 'ready';
  file_name?: string;
  size_bytes?: number;
  title?: string;
  generation_summary?: string;
  content_url?: string;
}

export const draftPrototypePath = (id: string) => documentPath(id) + '/prototype';

/** 读取当前草稿按修订号已生成的 HTML 原型（草稿版）。 */
export const getDraftPrototype = (id: string) =>
  apiClient.request<DraftPrototypeStatus>(draftPrototypePath(id));

/** 基于当前 PRD 草稿生成 HTML 原型——与冻结版走同一 Agent/skill 路线，仅允许未定稿时触发。 */
export const generateDraftPrototype = (id: string) =>
  apiClient.request<DraftPrototypeStatus>(draftPrototypePath(id), {
    method: 'POST', body: {}, timeoutMs: 30 * 60 * 1000,
  });

export type DraftPresentationAudience = 'team' | 'external';

export interface DraftPresentationStatus {
  document_id: string;
  revision: number;
  audience: DraftPresentationAudience;
  status: 'missing' | 'ready';
  file_name?: string;
  size_bytes?: number;
  title?: string;
  content_url?: string;
}

export const draftPresentationPath = (id: string, audience: DraftPresentationAudience) =>
  documentPath(id) + `/presentations/${audience}`;

/** 草稿 PPT 的在线预览页（新标签页打开，页内提供下载）。 */
export const draftPresentationPreviewUrl = (id: string, audience: DraftPresentationAudience) =>
  `${appConfig.apiBaseUrl}${draftPresentationPath(id, audience)}/preview`;

/** 读取当前草稿按修订号已生成的团队/对外 PPT（草稿版）。 */
export const getDraftPresentation = (id: string, audience: DraftPresentationAudience) =>
  apiClient.request<DraftPresentationStatus>(draftPresentationPath(id, audience));

/** 基于当前 PRD 草稿生成团队/对外 PPT——与冻结版走同一 Agent/skill 路线，仅允许未定稿时触发。 */
export const generateDraftPresentation = (id: string, audience: DraftPresentationAudience) =>
  apiClient.request<DraftPresentationStatus>(draftPresentationPath(id, audience), {
    method: 'POST', body: {}, timeoutMs: 30 * 60 * 1000,
  });

export interface VersionBaseline {
  version: number;
  created_at: string;
  blocks: Snapshot['blocks'];
}

/** 内容指纹：只看「章节 id + 正文」。标题变化由渲染层另行比较。 */
const contentSignature = (blocks: { id: string; content?: string }[]) => JSON.stringify(
  [...blocks].map(block => [block.id, block.content ?? ''])
    .sort((left, right) => (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0)),
);

/**
 * 找出「上一版」—— 版本列表里**内容与当前不同**的那个最大版本。
 *
 * 不能简单取 `max(version)`：每次改动都会同时推进 revision 并写入一份快照，
 * 所以最新快照往往**就等于当前内容**，直接拿它对比只会得到"没有任何变化"。
 * 这里从最新往回探，跳过与当前一致的快照（本机实测 v11 与当前逐字相同、v10 才是上一版）。
 */
export async function findPreviousVersion(
  documentId: string, current: { id: string; content?: string }[], depth = 4,
): Promise<VersionBaseline | null> {
  const target = contentSignature(current);
  const versions = [...await getDocumentVersions(documentId)].sort((left, right) => right.version - left.version);
  for (const row of versions.slice(0, depth)) {
    const detail = await getDocumentVersion(documentId, row.version);
    if (contentSignature(detail.snapshot.blocks) !== target)
      return { version: detail.version, created_at: detail.created_at, blocks: detail.snapshot.blocks };
  }
  return null;
}
