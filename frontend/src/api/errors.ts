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
  /** 2xx response that is not JSON, is malformed, loses precision or does not match the verified schema. */
  | 'invalid_response';

export type InvalidReason = 'not_json' | 'malformed_json' | 'unsafe_integer' | 'schema';

/** Where and why a 2xx response was rejected (no data is ever shown from it). */
export interface InvalidDetail {
  readonly reason: InvalidReason;
  /** Dotted path of the first offending field, e.g. "home_team.id". */
  readonly field?: string;
  /** 1-based position of the offending record in a list response. */
  readonly row?: number;
  /** The offending record's `id` exactly as received, when it was a plain integer. */
  readonly rowId?: string;
  /** The integer literal that could not be represented exactly. */
  readonly literal?: string;
}

export interface ApiErrorOptions {
  readonly status?: number;
  readonly detail?: string | null;
  readonly cause?: unknown;
  readonly invalid?: InvalidDetail;
  readonly timeoutMs?: number;
}

export class ApiError extends Error {
  override readonly name = 'ApiError';
  readonly kind: ApiErrorKind;
  /** HTTP status, when a response was received. */
  readonly status: number | null;
  /** Server-provided `detail` (FastAPI JSON error body), when present. */
  readonly detail: string | null;
  readonly invalid: InvalidDetail | null;
  readonly timeoutMs: number | null;

  constructor(kind: ApiErrorKind, message: string, options: ApiErrorOptions = {}) {
    super(message, { cause: options.cause });
    this.kind = kind;
    this.status = options.status ?? null;
    this.detail = options.detail ?? null;
    this.invalid = options.invalid ?? null;
    this.timeoutMs = options.timeoutMs ?? null;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

export function isNotFound(error: unknown): boolean {
  return isApiError(error) && error.kind === 'http' && error.status === 404;
}
