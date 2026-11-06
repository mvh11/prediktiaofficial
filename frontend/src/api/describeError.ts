import { isApiError } from './errors.ts';

export type FailureCategory = 'not_configured' | 'unavailable' | 'timeout' | 'not_found' | 'rejected' | 'invalid' | 'unexpected';

export interface FailureDescription {
  readonly category: FailureCategory;
  readonly title: string;
  readonly message: string;
  /** Whether retrying the same request could plausibly succeed. */
  readonly retryable: boolean;
}

/**
 * Maps any failure to honest, user-facing copy. Nothing here invents data; it
 * only explains why data is not shown.
 */
export function describeError(error: unknown, subject = 'this data'): FailureDescription {
  if (!isApiError(error)) {
    return {
      category: 'unexpected',
      title: 'Something went wrong',
      message: `An unexpected error prevented ${subject} from loading.`,
      retryable: true,
    };
  }
  switch (error.kind) {
    case 'not_configured':
      return {
        category: 'not_configured',
        title: 'Data source not configured',
        message: `This deployment has no PREDIKTIA data service configured, so ${subject} cannot be shown. Nothing is displayed in its place.`,
        retryable: false,
      };
    case 'network':
      return {
        category: 'unavailable',
        title: 'Data service unavailable',
        message: `The PREDIKTIA data service could not be reached, so ${subject} is unavailable right now.`,
        retryable: true,
      };
    case 'timeout':
      return {
        category: 'timeout',
        title: 'Request timed out',
        message: `The data service did not respond in time, so ${subject} is unavailable right now.`,
        retryable: true,
      };
    case 'invalid_response':
      return {
        category: 'invalid',
        title: 'Unexpected response',
        message: `The data service answered in an unexpected format. To avoid showing incorrect information, ${subject} is not displayed.`,
        retryable: true,
      };
    case 'http': {
      const status = error.status ?? 0;
      if (status === 404) {
        return { category: 'not_found', title: 'Not found', message: `${capitalize(subject)} does not exist in the PREDIKTIA data store.`, retryable: false };
      }
      // Non-JSON 5xx responses typically come from a proxy/gateway when the backend is down.
      if (status >= 500 && error.detail === null) {
        return {
          category: 'unavailable',
          title: 'Data service unavailable',
          message: `The PREDIKTIA data service is not responding correctly (HTTP ${status}), so ${subject} is unavailable right now.`,
          retryable: true,
        };
      }
      return {
        category: 'rejected',
        title: status >= 500 ? 'Data service error' : 'Request rejected',
        message: `The data service could not provide ${subject} (HTTP ${status}).`,
        retryable: status >= 500 || status === 429,
      };
    }
  }
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
