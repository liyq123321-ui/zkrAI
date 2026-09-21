import { appConfig } from './config';
import { ApiError, normalizeNetworkError, responseToApiError } from './errors';
import { clearSession, getToken } from '../auth/session';

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  // JSON request bodies stay structured until this shared boundary. Keeping
  // strings out of the type prevents callers from serializing twice and
  // turning an object into a JSON string that FastAPI rejects with 422.
  body?: object;
  timeoutMs?: number;
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string = appConfig.apiBaseUrl,
    private readonly defaultTimeoutMs = 20_000,
  ) {}

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const controller = new AbortController();
    let abortCause: 'timeout' | 'caller' | null = null;
    const timeoutId = globalThis.setTimeout(
      () => {
        if (!controller.signal.aborted) abortCause = 'timeout';
        controller.abort();
      },
      options.timeoutMs ?? this.defaultTimeoutMs,
    );
    const abortFromCaller = () => {
      if (!controller.signal.aborted) abortCause = 'caller';
      controller.abort();
    };
    options.signal?.addEventListener('abort', abortFromCaller, { once: true });

    try {
      const token = getToken();
      const response = await fetch(`${this.baseUrl}${path}`, {
        ...options,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        headers: {
          Accept: 'application/json',
          ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...options.headers,
        },
        signal: controller.signal,
      });
      if (!response.ok) {
        if (response.status === 401 && getToken()) {
          clearSession();
          globalThis.location.reload();
          throw new ApiError({
            status: 401,
            code: 'SESSION_EXPIRED',
            message: '登录已失效，请重新登录。',
            retryable: false,
          });
        }
        throw await responseToApiError(response);
      }
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    } catch (error) {
      if (abortCause === 'timeout') {
        throw new ApiError({
          status: 0,
          code: 'REQUEST_TIMEOUT',
          message: '请求处理超时，后台可能仍在执行。',
          retryable: true,
        });
      }
      throw normalizeNetworkError(error);
    } finally {
      globalThis.clearTimeout(timeoutId);
      options.signal?.removeEventListener('abort', abortFromCaller);
    }
  }
}

export const apiClient = new ApiClient();
