import { describe, expect, it } from 'vitest';
import { DEFAULT_API_BASE_URL, DEFAULT_API_TIMEOUT_MS, parseConfig } from './env.ts';

describe('parseConfig', () => {
  it('defaults to the same-origin /api base and the default timeout', () => {
    expect(parseConfig({})).toEqual({ apiBaseUrl: DEFAULT_API_BASE_URL, apiTimeoutMs: DEFAULT_API_TIMEOUT_MS, apiConfigError: null });
  });

  it('treats an explicitly empty base URL as "data source disabled"', () => {
    expect(parseConfig({ VITE_API_BASE_URL: '  ' })).toMatchObject({ apiBaseUrl: null, apiConfigError: null });
  });

  it('accepts relative paths and http(s) URLs, stripping trailing slashes', () => {
    expect(parseConfig({ VITE_API_BASE_URL: '/backend/' }).apiBaseUrl).toBe('/backend');
    expect(parseConfig({ VITE_API_BASE_URL: 'https://api.example.test/v1/' }).apiBaseUrl).toBe('https://api.example.test/v1');
  });

  it.each(['ftp://example.test', 'javascript:alert(1)', '//evil.test', 'api', 'https://x.test/?q=1'])(
    'rejects unsafe or ambiguous base URL %s and disables the data source',
    (value) => {
      const config = parseConfig({ VITE_API_BASE_URL: value });
      expect(config.apiBaseUrl).toBeNull();
      expect(config.apiConfigError).toMatch(/VITE_API_BASE_URL/);
    },
  );

  it.each([
    ['5000', 5000],
    ['999', DEFAULT_API_TIMEOUT_MS],
    ['60001', DEFAULT_API_TIMEOUT_MS],
    ['abc', DEFAULT_API_TIMEOUT_MS],
    ['1500.5', DEFAULT_API_TIMEOUT_MS],
    ['', DEFAULT_API_TIMEOUT_MS],
  ])('parses timeout %s as %d', (raw, expected) => {
    expect(parseConfig({ VITE_API_TIMEOUT_MS: raw }).apiTimeoutMs).toBe(expected);
  });
});
