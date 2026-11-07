import type { z } from 'zod';
import { ApiError, type InvalidDetail } from './errors.ts';
import { findIdentifierIssue, findUnsafeIntegerLiteral } from './jsonNumbers.ts';

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
 * Locates the first schema issue so the user can be told which record and
 * field broke the response (the whole response is still rejected).
 */
export function locateIssue(issues: readonly z.core.$ZodIssue[], body: unknown): InvalidDetail {
  const path = issues[0]?.path ?? [];
  const [first, ...rest] = path;
  const fieldOf = (parts: readonly PropertyKey[]) => {
    const field = parts.map(String).join('.');
    return field === '' ? {} : { field };
  };
  if (Array.isArray(body) && typeof first === 'number') {
    const record: unknown = body[first];
    const id = typeof record === 'object' && record !== null && 'id' in record ? record.id : undefined;
    const rowId = typeof id === 'number' && Number.isSafeInteger(id) ? String(id) : undefined;
    return { reason: 'schema', row: first + 1, ...fieldOf(rest), ...(rowId !== undefined ? { rowId } : {}) };
  }
  return { reason: 'schema', ...fieldOf(path) };
}

/**
 * Centralized HTTP client. Responsibilities:
 * - explicit "not configured" short-circuit (no request is made);
 * - per-request timeout via AbortController, covering headers AND body;
 * - caller cancellation that is distinguishable from timeouts;
 * - precision guards on the raw text: integers JSON cannot represent exactly, and
 *   identifier tokens that are not canonical in-range integers, reject the response
 *   (JSON.parse would otherwise round 12.9999999999999999 to 13 without a trace);
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
          throw new ApiError('timeout', `No response within ${timeoutMs} ms.`, { cause, timeoutMs });
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
          throw new ApiError('invalid_response', 'The data service did not return JSON.', {
            status: response.status,
            invalid: { reason: 'not_json' },
          });
        }

        let text: string;
        try {
          text = await response.text();
        } catch (cause) {
          if (controller.signal.aborted) rethrowAbort(cause);
          throw new ApiError('network', 'The response body could not be read.', { cause });
        }

        const unsafe = findUnsafeIntegerLiteral(text);
        if (unsafe !== null) {
          throw new ApiError('invalid_response', `The response contains an integer that cannot be represented exactly: ${unsafe}.`, {
            status: response.status,
            invalid: { reason: 'unsafe_integer', literal: unsafe },
          });
        }

        let body: unknown;
        try {
          body = JSON.parse(text);
        } catch (cause) {
          throw new ApiError('invalid_response', 'The data service returned malformed JSON.', {
            status: response.status,
            cause,
            invalid: { reason: 'malformed_json' },
          });
        }

        // Valid JSON from here on: check identifier tokens before trusting any parsed number.
        const identifier = findIdentifierIssue(text);
        if (identifier !== null) {
          throw new ApiError('invalid_response', `Identifier ${identifier.key} is not a canonical in-range integer: ${identifier.literal}.`, {
            status: response.status,
            invalid: {
              reason: 'invalid_identifier',
              field: identifier.key,
              literal: identifier.literal,
              ...(identifier.row !== undefined ? { row: identifier.row } : {}),
            },
          });
        }

        const parsed = schema.safeParse(body);
        if (!parsed.success) {
          throw new ApiError('invalid_response', 'The data service response did not match the expected format.', {
            status: response.status,
            cause: parsed.error,
            invalid: locateIssue(parsed.error.issues, body),
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
