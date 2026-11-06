import type { z } from 'zod';
import { ApiError } from './errors.ts';

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  /** Cancels the request (e.g. component unmount). Rejects with the abort reason, not an ApiError. */
  readonly signal?: AbortSignal | undefined;
  readonly query?: Readonly<Record<string, QueryValue>>;
}

export interface HttpClient {
  /** GET a JSON resource and validate it against `schema` before returning it. */
  getJson<S extends z.ZodType>(path: string, schema: S, options?: RequestOptions): Promise<z.output<S>>;
}

export interface HttpClientOptions {
  readonly baseUrl: string | null;
  readonly timeoutMs: number;
  readonly fetchImpl?: typeof fetch;
}

const TIMEOUT_REASON = Symbol('prediktia.timeout');

export function buildUrl(baseUrl: string, path: string, query?: Readonly<Record<string, QueryValue>>): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === '') continue;
    params.append(key, String(value));
  }
  const search = params.toString();
  return `${baseUrl}${normalizedPath}${search ? `?${search}` : ''}`;
}

function isJsonContentType(response: Response): boolean {
  const type = response.headers.get('content-type') ?? '';
  return /^application\/(.+\+)?json\b/i.test(type.trim());
}

async function readErrorDetail(response: Response): Promise<string | null> {
  if (!isJsonContentType(response)) return null;
  try {
    const body: unknown = await response.json();
    if (typeof body === 'object' && body !== null && 'detail' in body) {
      const { detail } = body;
      if (typeof detail === 'string') return detail;
    }
  } catch {
    // Unreadable error body: fall through to a generic HTTP error.
  }
  return null;
}

/**
 * Centralized HTTP client. Responsibilities:
 * - explicit "not configured" short-circuit (no request is made);
 * - per-request timeout via AbortController, covering headers AND body;
 * - caller cancellation that is distinguishable from timeouts;
 * - strict JSON + schema validation, so unverified shapes never reach the UI.
 */
export function createHttpClient({ baseUrl, timeoutMs, fetchImpl }: HttpClientOptions): HttpClient {
  return {
    async getJson(path, schema, options = {}) {
      if (baseUrl === null) {
        throw new ApiError('not_configured', 'No API base URL is configured; the data source is disabled.');
      }
      const { signal: callerSignal, query } = options;
      callerSignal?.throwIfAborted();

      const doFetch = fetchImpl ?? globalThis.fetch.bind(globalThis);
      const controller = new AbortController();
      const timer = setTimeout(() => {
        controller.abort(TIMEOUT_REASON);
      }, timeoutMs);
      const onCallerAbort = () => {
        controller.abort(callerSignal?.reason);
      };
      callerSignal?.addEventListener('abort', onCallerAbort, { once: true });

      const rethrowAbort = (cause: unknown): never => {
        if (controller.signal.reason === TIMEOUT_REASON) {
          throw new ApiError('timeout', `No response within ${timeoutMs} ms.`, { cause });
        }
        // Caller cancelled: propagate the caller's own reason untouched.
        throw callerSignal?.reason ?? cause;
      };

      try {
        let response: Response;
        try {
          response = await doFetch(buildUrl(baseUrl, path, query), {
            method: 'GET',
            headers: { Accept: 'application/json' },
            signal: controller.signal,
          });
        } catch (cause) {
          if (controller.signal.aborted) rethrowAbort(cause);
          throw new ApiError('network', 'The data service could not be reached.', { cause });
        }

        if (!response.ok) {
          const detail = await readErrorDetail(response);
          if (controller.signal.aborted) rethrowAbort(undefined);
          throw new ApiError('http', `Request failed with HTTP ${response.status}.`, { status: response.status, detail });
        }

        if (!isJsonContentType(response)) {
          throw new ApiError('invalid_response', 'The data service did not return JSON.', { status: response.status });
        }

        let body: unknown;
        try {
          body = await response.json();
        } catch (cause) {
          if (controller.signal.aborted) rethrowAbort(cause);
          throw new ApiError('invalid_response', 'The data service returned malformed JSON.', { status: response.status, cause });
        }

        const parsed = schema.safeParse(body);
        if (!parsed.success) {
          throw new ApiError('invalid_response', 'The data service response did not match the expected format.', {
            status: response.status,
            cause: parsed.error,
          });
        }
        return parsed.data;
      } finally {
        clearTimeout(timer);
        callerSignal?.removeEventListener('abort', onCallerAbort);
      }
    },
  };
}
