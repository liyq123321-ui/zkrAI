import { apiClient } from './client';

export type WorkspaceAsset = {
  kind: 'prd' | 'prototype' | 'code' | 'knowledge' | 'skill';
  name: string;
  folder: string;
  status: 'ready' | 'not_generated' | 'reserved';
  revision: number | null;
  source_url: string | null;
};

export type WorkspaceFile = {
  id: string;
  name: string;
  folder_path: string;
  file_type: string;
  parse_status: string;
  chunk_count: number;
  updated_at: string | null;
};

export type WorkspaceSpace = {
  kind: 'personal' | 'project';
  project_id: string | null;
  session_id: string | null;
  name: string;
  description: string;
  phase: string;
  tenant_id: number | null;
  remote_status: 'ready' | 'not_synced' | 'workspace_only' | 'degraded';
  knowledge_base_id: string | null;
  files: WorkspaceFile[];
  assets: WorkspaceAsset[];
  storage_used?: number;
  storage_quota?: number;
  created_at: string | null;
};

export type WorkspaceKnowledgeBase = {
  id: string;
  tenant_id: number;
  name: string;
  description: string;
  knowledge_count: number;
  chunk_count: number;
  processing_count: number;
  updated_at: string | null;
};

export type WorkspaceSkill = {
  id: string;
  name: string;
  domain: string;
  capability: string;
  description: string;
  version: string | null;
  installed: boolean;
  file_count: number;
};

export type WorkspaceOverview = {
  actor_id: string;
  connection: {
    status: 'online' | 'offline';
    base_url: string;
    web_url: string;
    message: string;
  };
  spaces: WorkspaceSpace[];
  knowledge_bases: WorkspaceKnowledgeBase[];
  skills: WorkspaceSkill[];
  sync_result?: {
    created_spaces: number;
    uploaded_files: number;
    project_count: number;
  };
};

export const getWorkspaceOverview = (actorId: string, signal?: AbortSignal) =>
  apiClient.request<WorkspaceOverview>(
    `/workspace/overview?actor_id=${encodeURIComponent(actorId)}`,
    { signal, timeoutMs: 60_000 },
  );

export const syncWorkspace = (actorId: string) =>
  apiClient.request<WorkspaceOverview>(
    `/workspace/sync?actor_id=${encodeURIComponent(actorId)}`,
    { method: 'POST', timeoutMs: 240_000 },
  );

export const getKnowledgeDocuments = (
  knowledgeBaseId: string,
  tenantId: number,
  signal?: AbortSignal,
) => apiClient.request<{ items: WorkspaceFile[]; total: number }>(
  `/workspace/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents?tenant_id=${tenantId}`,
  { signal, timeoutMs: 60_000 },
);

export const getSkillFiles = (skillId: string, signal?: AbortSignal) =>
  apiClient.request<{ items: Array<{ path: string; size: number; type: string }> }>(
    `/workspace/skills/${encodeURIComponent(skillId)}/files`,
    { signal, timeoutMs: 60_000 },
  );
