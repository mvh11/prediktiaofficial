import { isApiError, type InvalidDetail } from './errors.ts';

export type FailureCategory =
  | 'not_configured'
  | 'offline'
  | 'unavailable'
  | 'timeout'
  | 'not_found'
  | 'rejected'
  | 'invalid'
  | 'unexpected';

/**
 * Language-agnostic classification of a failure. The UI turns it into text
 * (see components/ui/ApiFailure.tsx); nothing here invents data, it only
 * explains why data is not shown.
 */
export interface FailureInfo {
  readonly category: FailureCategory;
  /** Whether retrying the same request could plausibly succeed. */
  readonly retryable: boolean;
  readonly status: number | null;
  readonly timeoutMs: number | null;
  readonly invalid: InvalidDetail | null;
}

export interface DescribeContext {
  /** `navigator.onLine`; a network failure while offline is reported as such. */
  readonly online?: boolean;
}

function info(category: FailureCategory, retryable: boolean, extra: Partial<Omit<FailureInfo, 'category' | 'retryable'>> = {}): FailureInfo {
  return { category, retryable, status: extra.status ?? null, timeoutMs: extra.timeoutMs ?? null, invalid: extra.invalid ?? null };
}

export function describeError(error: unknown, context: DescribeContext = {}): FailureInfo {
  if (!isApiError(error)) return info('unexpected', true);
  switch (error.kind) {
    case 'not_configured':
      return info('not_configured', false);
    case 'network':
      return info(context.online === false ? 'offline' : 'unavailable', true);
    case 'timeout':
      return info('timeout', true, { timeoutMs: error.timeoutMs });
    case 'invalid_response':
      return info('invalid', true, { status: error.status, invalid: error.invalid });
    case 'http': {
      const status = error.status ?? 0;
      if (status === 404 && error.detail !== null) return info('not_found', false, { status });
      // Non-JSON 404/5xx responses come from a proxy, gateway or static server, not from the
      // backend: the API is unreachable or misrouted, which says nothing about the record.
      if ((status === 404 || status >= 500) && error.detail === null) return info('unavailable', true, { status });
      return info('rejected', status >= 500 || status === 429, { status });
    }
  }
}
