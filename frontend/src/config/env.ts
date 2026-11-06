/**
 * Explicit, typed runtime configuration.
 *
 * Every value comes from Vite's `import.meta.env` (VITE_* variables, embedded at
 * build time). Invalid values never crash the app: they fall back to a safe
 * default or disable the data source, and the UI reports it honestly.
 */

export const DEFAULT_API_BASE_URL = '/api';
export const DEFAULT_API_TIMEOUT_MS = 10_000;
export const MIN_API_TIMEOUT_MS = 1_000;
export const MAX_API_TIMEOUT_MS = 60_000;

export interface AppConfig {
  /** Base URL for API calls, without trailing slash. `null` = data source disabled. */
  readonly apiBaseUrl: string | null;
  readonly apiTimeoutMs: number;
  /** Human-readable reason when the configured base URL was rejected. */
  readonly apiConfigError: string | null;
}

export interface RawEnv {
  readonly VITE_API_BASE_URL?: string | undefined;
  readonly VITE_API_TIMEOUT_MS?: string | undefined;
}

function parseBaseUrl(raw: string | undefined): Pick<AppConfig, 'apiBaseUrl' | 'apiConfigError'> {
  if (raw === undefined) return { apiBaseUrl: DEFAULT_API_BASE_URL, apiConfigError: null };
  const value = raw.trim();
  if (value === '') return { apiBaseUrl: null, apiConfigError: null };

  const isRelative = value.startsWith('/') && !value.startsWith('//');
  let isAbsoluteHttp = false;
  if (!isRelative) {
    try {
      const url = new URL(value);
      isAbsoluteHttp = (url.protocol === 'http:' || url.protocol === 'https:') && url.search === '' && url.hash === '';
    } catch {
      isAbsoluteHttp = false;
    }
  }
  if (!isRelative && !isAbsoluteHttp) {
    return {
      apiBaseUrl: null,
      apiConfigError: 'VITE_API_BASE_URL must be a same-origin path (e.g. /api) or an http(s) URL.',
    };
  }
  return { apiBaseUrl: value.replace(/\/+$/, ''), apiConfigError: null };
}

function parseTimeout(raw: string | undefined): number {
  if (raw === undefined || raw.trim() === '') return DEFAULT_API_TIMEOUT_MS;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < MIN_API_TIMEOUT_MS || value > MAX_API_TIMEOUT_MS) {
    return DEFAULT_API_TIMEOUT_MS;
  }
  return value;
}

export function parseConfig(env: RawEnv): AppConfig {
  return { ...parseBaseUrl(env.VITE_API_BASE_URL), apiTimeoutMs: parseTimeout(env.VITE_API_TIMEOUT_MS) };
}

export const appConfig: AppConfig = parseConfig(import.meta.env);
