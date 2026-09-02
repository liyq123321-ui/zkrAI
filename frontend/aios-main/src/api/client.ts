import { appConfig } from './config';
import { normalizeNetworkError, responseToApiError } from './errors';

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
  timeoutMs?: number;
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string = appConfig.apiBaseUrl,
    private readonly defaultTimeoutMs = 20_000,
  ) {}

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const controller = new AbortController();
    const timeoutId = globalThis.setTimeout(
      () => controller.abort(),
      options.timeoutMs ?? this.defaultTimeoutMs,
    );
    const abortFromCaller = () => controller.abort();
    options.signal?.addEventListener('abort', abortFromCaller, { once: true });

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        ...options,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        headers: {
          Accept: 'application/json',
          ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }),
          ...options.headers,
        },
        signal: controller.signal,
      });
      if (!response.ok) throw await responseToApiError(response);
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    } catch (error) {
      throw normalizeNetworkError(error);
    } finally {
      globalThis.clearTimeout(timeoutId);
      options.signal?.removeEventListener('abort', abortFromCaller);
    }
  }
}

export const apiClient = new ApiClient();
