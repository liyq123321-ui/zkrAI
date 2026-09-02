export interface ApiErrorShape {
  status: number;
  code: string;
  message: string;
  validationErrors?: unknown[];
  retryable: boolean;
}

export class ApiError extends Error implements ApiErrorShape {
  readonly status: number;
  readonly code: string;
  readonly validationErrors?: unknown[];
  readonly retryable: boolean;

  constructor(shape: ApiErrorShape) {
    super(shape.message);
    this.name = 'ApiError';
    this.status = shape.status;
    this.code = shape.code;
    this.validationErrors = shape.validationErrors;
    this.retryable = shape.retryable;
  }
}

interface ErrorEnvelope {
  detail?: {
    code?: unknown;
    message?: unknown;
    errors?: unknown;
  };
}

export async function responseToApiError(response: Response): Promise<ApiError> {
  let body: ErrorEnvelope = {};
  try {
    body = (await response.json()) as ErrorEnvelope;
  } catch {
    // Non-JSON proxy/server errors still receive one stable client representation.
  }
  const detail = body.detail;
  const validationErrors = Array.isArray(detail?.errors) ? detail.errors : undefined;
  return new ApiError({
    status: response.status,
    code: typeof detail?.code === 'string' ? detail.code : `HTTP_${response.status}`,
    message:
      typeof detail?.message === 'string'
        ? detail.message
        : response.statusText || '请求失败，请稍后重试。',
    validationErrors,
    retryable: response.status === 0 || response.status === 429 || response.status >= 500,
  });
}

export function normalizeNetworkError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === 'AbortError') {
    return new ApiError({
      status: 0,
      code: 'REQUEST_ABORTED',
      message: '请求已取消。',
      retryable: false,
    });
  }
  return new ApiError({
    status: 0,
    code: 'NETWORK_ERROR',
    message: '无法连接后端，请检查服务地址和网络状态。',
    retryable: true,
  });
}
