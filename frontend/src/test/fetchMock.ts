import { vi } from 'vitest';

export type RouteHandler = (url: URL, init: RequestInit | undefined) => Response | Promise<Response>;

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

export function textResponse(body: string, status: number, contentType = 'text/plain'): Response {
  return new Response(body, { status, headers: { 'Content-Type': contentType } });
}

/** A fetch that never settles unless its signal aborts (simulates a hung backend). */
export function hangingResponse(_url: URL, init: RequestInit | undefined): Promise<Response> {
  return new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => {
      reject(init.signal?.reason instanceof Error ? init.signal.reason : new DOMException('Aborted', 'AbortError'));
    });
  });
}

export function networkFailure(): never {
  throw new TypeError('Failed to fetch');
}

/**
 * Installs a global fetch stub that dispatches on the request path (after the
 * `/api` prefix). Unmatched requests fail the test loudly instead of hanging.
 */
export function mockFetch(routes: Record<string, RouteHandler>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const raw = input instanceof Request ? input.url : String(input);
    const url = new URL(raw, 'http://localhost');
    const path = url.pathname.replace(/^\/api/, '');
    const handler = routes[path] ?? Object.entries(routes).find(([pattern]) => pattern.endsWith('*') && path.startsWith(pattern.slice(0, -1)))?.[1];
    if (handler === undefined) {
      return Promise.reject(new Error(`Unexpected request in test: ${url.pathname}${url.search}`));
    }
    return Promise.resolve().then(() => handler(url, init));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** Requested URLs (path + query) for assertions. */
export function requestedUrls(fetchMock: ReturnType<typeof mockFetch>): URL[] {
  return fetchMock.mock.calls.map(([input]) => new URL(input instanceof Request ? input.url : String(input), 'http://localhost'));
}
