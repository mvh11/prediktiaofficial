/**
 * Typed failures of the HTTP layer. UI code never inspects raw fetch errors:
 * it receives an ApiError and maps it to an honest user-facing state.
 */
export type ApiErrorKind =
  /** No API base URL configured: no request was attempted. */
  | 'not_configured'
  /** The request never produced an HTTP response (offline, DNS, refused). */
  | 'network'
  /** No complete response within the configured timeout. */
  | 'timeout'
  /** HTTP response with a non-2xx status. */
  | 'http'
  /** 2xx response that is not JSON or does not match the verified schema. */
  | 'invalid_response';

export class ApiError extends Error {
  override readonly name = 'ApiError';
  readonly kind: ApiErrorKind;
  /** HTTP status, when a response was received. */
  readonly status: number | null;
  /** Server-provided `detail` (FastAPI JSON error body), when present. */
  readonly detail: string | null;

  constructor(kind: ApiErrorKind, message: string, options: { status?: number; detail?: string | null; cause?: unknown } = {}) {
    super(message, { cause: options.cause });
    this.kind = kind;
    this.status = options.status ?? null;
    this.detail = options.detail ?? null;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

export function isNotFound(error: unknown): boolean {
  return isApiError(error) && error.kind === 'http' && error.status === 404;
}
