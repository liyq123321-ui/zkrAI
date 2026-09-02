export type DataMode = 'mock' | 'api';

function parseDataMode(raw: string | undefined): DataMode {
  const value = (raw ?? 'mock').trim().toLowerCase();
  if (value === 'mock' || value === 'api') return value;
  throw new Error(`VITE_DATA_MODE 必须是 mock 或 api，当前值为 ${raw}`);
}

function normalizeBaseUrl(raw: string | undefined): string {
  const value = (raw ?? 'http://127.0.0.1:8088').trim();
  if (!value) throw new Error('VITE_API_BASE_URL 不能为空');
  return value.replace(/\/$/, '');
}

export const appConfig = Object.freeze({
  dataMode: parseDataMode(import.meta.env.VITE_DATA_MODE),
  apiBaseUrl: normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL),
});
