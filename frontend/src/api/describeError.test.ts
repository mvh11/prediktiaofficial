import { describe, expect, it } from 'vitest';
import { failureText } from '../i18n/failureText.ts';
import { esCL } from '../i18n/messages.es-CL.ts';
import { describeError } from './describeError.ts';
import { ApiError } from './errors.ts';

describe('describeError', () => {
  it.each([
    [new ApiError('not_configured', 'x'), 'not_configured', false],
    [new ApiError('network', 'x'), 'unavailable', true],
    [new ApiError('timeout', 'x', { timeoutMs: 10_000 }), 'timeout', true],
    [new ApiError('invalid_response', 'x'), 'invalid', true],
    [new ApiError('http', 'x', { status: 404, detail: 'nope' }), 'not_found', false],
    [new ApiError('http', 'x', { status: 404, detail: null }), 'unavailable', true],
    [new ApiError('http', 'x', { status: 502, detail: null }), 'unavailable', true],
    [new ApiError('http', 'x', { status: 500, detail: 'boom' }), 'rejected', true],
    [new ApiError('http', 'x', { status: 422, detail: 'bad' }), 'rejected', false],
    [new ApiError('http', 'x', { status: 429, detail: 'slow down' }), 'rejected', true],
    [new Error('random'), 'unexpected', true],
  ] as const)('maps %o to %s', (error, category, retryable) => {
    const failure = describeError(error);
    expect(failure.category).toBe(category);
    expect(failure.retryable).toBe(retryable);
    const text = failureText(failure, 'la lista de partidos', esCL);
    expect(text.title.length).toBeGreaterThan(0);
    expect(text.paragraphs.join(' ')).not.toMatch(/undefined|NaN/);
  });

  it('reports a network failure while the browser is offline as "offline"', () => {
    expect(describeError(new ApiError('network', 'x'), { online: false }).category).toBe('offline');
    expect(describeError(new ApiError('network', 'x'), { online: true }).category).toBe('unavailable');
  });

  it('never echoes server-provided detail text to the user', () => {
    const failure = describeError(new ApiError('http', 'x', { status: 500, detail: '<script>secret</script>' }));
    expect(failureText(failure, 'la lista', esCL).paragraphs.join(' ')).not.toContain('secret');
  });
});

describe('failureText (es-CL)', () => {
  it('states the timeout in seconds', () => {
    const text = failureText(describeError(new ApiError('timeout', 'x', { timeoutMs: 10_000 })), 'la lista de partidos', esCL);
    expect(text.title).toBe('La solicitud tardó demasiado');
    expect(text.paragraphs[0]).toContain('10 segundos');
  });

  it('explains which record and field invalidated the response, and that nothing was silently removed', () => {
    const error = new ApiError('invalid_response', 'x', { invalid: { reason: 'schema', row: 4, field: 'kickoff_at', rowId: '123' } });
    const text = failureText(describeError(error), 'la lista de partidos', esCL);
    expect(text.title).toBe('Respuesta con formato inesperado');
    expect(text.paragraphs.join(' ')).toContain('registro n.º 4');
    expect(text.paragraphs.join(' ')).toContain('identificador 123');
    expect(text.paragraphs.join(' ')).toContain('«kickoff_at»');
    expect(text.paragraphs.join(' ')).toMatch(/no elimina ni corrige datos en silencio/);
  });

  it('names an integer that cannot be represented exactly', () => {
    const error = new ApiError('invalid_response', 'x', { invalid: { reason: 'unsafe_integer', literal: '9007199254740993' } });
    const text = failureText(describeError(error), 'este partido', esCL);
    expect(text.title).toBe('Identificador numérico no representable');
    expect(text.paragraphs[0]).toContain('9007199254740993');
  });

  it('distinguishes a gateway outage (status) from an unreachable service', () => {
    expect(failureText(describeError(new ApiError('http', 'x', { status: 502 })), 'la lista', esCL).paragraphs[0]).toContain('HTTP 502');
    expect(failureText(describeError(new ApiError('network', 'x')), 'la lista', esCL).paragraphs[0]).not.toContain('HTTP');
  });
});
