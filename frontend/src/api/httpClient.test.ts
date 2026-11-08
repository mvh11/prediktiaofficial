import { describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { hangingResponse, jsonResponse, textResponse } from '../test/fetchMock.ts';
import { ApiError } from './errors.ts';
import { buildUrl, createHttpClient } from './httpClient.ts';
import { fixtureListSchema, fixtureSchema } from './schemas.ts';
import { devFixture } from '../test/devOnlyData.ts';

const schema = z.object({ status: z.literal('ok') });

// The default timeout is far above any stubbed response time (CPU starvation must not turn a success into a timeout);
// the timeout tests pass a small value explicitly.
function clientWith(fetchImpl: typeof fetch, timeoutMs = 30_000) {
  return createHttpClient({ baseUrl: '/api', timeoutMs, fetchImpl });
}

async function captureError(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('Expected the promise to reject');
}

describe('buildUrl', () => {
  it('joins base and path and omits empty query values', () => {
    expect(buildUrl('/api', '/fixtures', { a: 1, b: undefined, c: null, d: '', e: false, f: 'x y' })).toBe('/api/fixtures?a=1&e=false&f=x+y');
    expect(buildUrl('https://h.test', 'health')).toBe('https://h.test/health');
  });
});

describe('createHttpClient', () => {
  it('never calls fetch when no base URL is configured', async () => {
    const fetchImpl = vi.fn<typeof fetch>();
    const client = createHttpClient({ baseUrl: null, timeoutMs: 1_000, fetchImpl });
    const error = await captureError(client.getJson('/health', schema));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: 'not_configured' });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('returns schema-validated JSON and sends an Accept header', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: 'ok', extra: 'stripped' }));
    await expect(clientWith(fetchImpl).getJson('/health', schema, { query: { x: 1 } })).resolves.toEqual({ status: 'ok' });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe('/api/health?x=1');
    expect(init?.method).toBe('GET');
    expect(new Headers(init?.headers).get('Accept')).toBe('application/json');
  });

  it('maps a fetch TypeError to a network error', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(clientWith(fetchImpl).getJson('/health', schema)).rejects.toMatchObject({ kind: 'network' });
  });

  it('times out a request that never responds, and reports the configured timeout', async () => {
    const fetchImpl = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => hangingResponse(new URL('http://x'), init));
    const error = await captureError(clientWith(fetchImpl, 30).getJson('/health', schema));
    expect(error).toMatchObject({ kind: 'timeout', timeoutMs: 30 });
  });

  it('times out while reading a slow body', async () => {
    const fetchImpl = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      const body = new ReadableStream({
        start(controller) {
          init?.signal?.addEventListener('abort', () => {
            controller.error(new DOMException('Aborted', 'AbortError'));
          });
        },
      });
      return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } }));
    });
    await expect(clientWith(fetchImpl, 30).getJson('/health', schema)).rejects.toMatchObject({ kind: 'timeout' });
  });

  it('propagates caller cancellation as the abort reason, not as an ApiError', async () => {
    const fetchImpl = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => hangingResponse(new URL('http://x'), init));
    const controller = new AbortController();
    const pending = clientWith(fetchImpl).getJson('/health', schema, { signal: controller.signal });
    const reason = new DOMException('Component unmounted', 'AbortError');
    controller.abort(reason);
    const error = await captureError(pending);
    expect(error).toBe(reason);
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it('does not call fetch for an already-aborted signal', async () => {
    const fetchImpl = vi.fn<typeof fetch>();
    const controller = new AbortController();
    controller.abort();
    await expect(clientWith(fetchImpl).getJson('/health', schema, { signal: controller.signal })).rejects.toBeDefined();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('exposes FastAPI JSON error details with the HTTP status', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: 'Partido no encontrado' }, 404));
    await expect(clientWith(fetchImpl).getJson('/fixtures/1', schema)).rejects.toMatchObject({
      kind: 'http',
      status: 404,
      detail: 'Partido no encontrado',
    });
  });

  it('reports non-JSON error bodies (e.g. proxy errors) with a null detail', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(textResponse('Bad Gateway', 502));
    await expect(clientWith(fetchImpl).getJson('/health', schema)).rejects.toMatchObject({ kind: 'http', status: 502, detail: null });
  });

  it('rejects a 2xx HTML response (e.g. an SPA fallback page) as invalid', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(textResponse('<!doctype html>', 200, 'text/html'));
    await expect(clientWith(fetchImpl).getJson('/health', schema)).rejects.toMatchObject({ kind: 'invalid_response', invalid: { reason: 'not_json' } });
  });

  it('rejects malformed JSON', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(textResponse('{"status":', 200, 'application/json'));
    await expect(clientWith(fetchImpl).getJson('/health', schema)).rejects.toMatchObject({ kind: 'invalid_response', invalid: { reason: 'malformed_json' } });
  });

  it('rejects JSON that does not match the schema instead of rendering it', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: 'degraded' }));
    await expect(clientWith(fetchImpl).getJson('/health', schema)).rejects.toMatchObject({ kind: 'invalid_response' });
  });

  it('rejects an integer that JSON.parse would silently round, naming the exact literal', async () => {
    const numberSchema = z.object({ id: z.number() });
    // 9007199254740993 parses to 9007199254740992: the response must not be shown at all.
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(textResponse('{"id":9007199254740993}', 200, 'application/json'));
    const error = await captureError(clientWith(fetchImpl).getJson('/x', numberSchema));
    expect(error).toMatchObject({ kind: 'invalid_response', invalid: { reason: 'unsafe_integer', literal: '9007199254740993' } });
  });

  it('locates the first offending record of a list response (1-based row, field and id)', async () => {
    const rows = [devFixture(1), { ...devFixture(7), kickoff_at: '2030-01-05T15:00:00' }, devFixture(3)];
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(rows));
    const error = await captureError(clientWith(fetchImpl).getJson('/fixtures', fixtureListSchema));
    expect(error).toMatchObject({ kind: 'invalid_response', invalid: { reason: 'schema', row: 2, field: 'kickoff_at', rowId: '7' } });
  });

  it('locates a nested field and omits a non-integer id', async () => {
    // A string id is not a number token (the identifier guard ignores strings); the schema rejects it.
    const rows = [{ ...devFixture(1), id: 'x', home_team: { id: 5, name: '', logo_url: null } }];
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(rows));
    const error = await captureError(clientWith(fetchImpl).getJson('/fixtures', fixtureListSchema));
    expect(error).toBeInstanceOf(ApiError);
    const invalid = (error as ApiError).invalid;
    expect(invalid).toMatchObject({ reason: 'schema', row: 1 });
    expect(invalid?.rowId).toBeUndefined();
  });

  // D1 (independent QA): JSON.parse rounds near-integers and accepts exponents, so an identifier token must be
  // checked on the raw text. Each case would otherwise arrive as a "valid" integer id.
  it.each([
    ['12.9999999999999999', 13],
    ['41.99999999999999999', 42],
    ['1.0000000000000001', 1],
    ['1.3e1', 13],
    ['1E0', 1],
    ['2.0', 2],
  ])('rejects the list identifier token %s instead of reading it as %d', async (token, rounded) => {
    expect(JSON.parse(`{"id":${token}}`)).toEqual({ id: rounded }); // the loss being guarded against
    const text = JSON.stringify([devFixture(1), devFixture(2)]).replace('"id":2,', `"id":${token},`);
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(textResponse(text, 200, 'application/json'));
    const error = await captureError(clientWith(fetchImpl).getJson('/fixtures', fixtureListSchema));
    expect(error).toMatchObject({ kind: 'invalid_response', invalid: { reason: 'invalid_identifier', field: 'id', literal: token, row: 2 } });
  });

  it.each([
    ['competition_id', '1.0000000000000001'],
    ['competition_id', '0'],
    ['competition_id', '-1'],
    ['external_id', '700001.0'],
    ['external_id', '2147483648'],
    ['external_id', '-0'],
  ])('rejects %s token %s in a detail response', async (key, token) => {
    const text = JSON.stringify(devFixture(1)).replace(new RegExp(`"${key}":-?\\d+`), `"${key}":${token}`);
    expect(text).toContain(`"${key}":${token}`);
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(textResponse(text, 200, 'application/json'));
    const error = await captureError(clientWith(fetchImpl).getJson('/fixtures/1', fixtureSchema));
    expect(error).toMatchObject({ kind: 'invalid_response', invalid: { reason: 'invalid_identifier', field: key, literal: token } });
    expect((error as ApiError).invalid?.row).toBeUndefined();
  });

  it('checks nested team ids and keeps strings that merely look like JSON numbers', async () => {
    const nested = JSON.stringify(devFixture(1)).replace(/"home_team":\{"id":\d+/, '"home_team":{"id":11.000000000000001');
    const fetchNested = vi.fn<typeof fetch>().mockResolvedValue(textResponse(nested, 200, 'application/json'));
    expect(await captureError(clientWith(fetchNested).getJson('/fixtures/1', fixtureSchema))).toMatchObject({
      invalid: { reason: 'invalid_identifier', field: 'id', literal: '11.000000000000001' },
    });

    const lookalike = devFixture(1, { venue_name: '{"id":12.9999999999999999}', referee: '"id": 1e3' });
    const fetchOk = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(lookalike));
    await expect(clientWith(fetchOk).getJson('/fixtures/1', fixtureSchema)).resolves.toMatchObject({
      id: 1,
      venue_name: '{"id":12.9999999999999999}',
      referee: '"id": 1e3',
    });
  });

  it('accepts in-range canonical identifiers, including the PostgreSQL integer maximum', async () => {
    const teams = { home_team: { id: 2147483647, name: 'A', logo_url: null }, away_team: { id: 1, name: 'B', logo_url: null } };
    const body = devFixture(2147483647, { ...teams, competition_id: 2147483647, external_id: -2147483648 });
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    await expect(clientWith(fetchImpl).getJson('/fixtures/2147483647', fixtureSchema)).resolves.toMatchObject({ id: 2147483647 });
  });
});
