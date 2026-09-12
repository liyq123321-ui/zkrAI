import { apiClient } from '../client';
import type { Snapshot } from './types';
export const documentPath = (id: string) => `/prd-documents/${encodeURIComponent(id)}`;
export const openDocument = (sessionId: string) => apiClient.request<Snapshot>('/prd-documents', { method: 'POST', body: { session_id: sessionId } });
export const getDocument = (id: string) => apiClient.request<Snapshot>(documentPath(id));
export const mutate = <T = Snapshot>(id: string, path: string, body: object, method = 'POST') =>
  apiClient.request<T>(documentPath(id) + path, { method, body });
