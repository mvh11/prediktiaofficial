import { describe, expect, it } from 'vitest';
import { describeError } from './describeError.ts';
import { ApiError } from './errors.ts';

describe('describeError', () => {
  it.each([
    [new ApiError('not_configured', 'x'), 'not_configured', false],
    [new ApiError('network', 'x'), 'unavailable', true],
    [new ApiError('timeout', 'x'), 'timeout', true],
    [new ApiError('invalid_response', 'x'), 'invalid', true],
    [new ApiError('http', 'x', { status: 404, detail: 'nope' }), 'not_found', false],
    [new ApiError('http', 'x', { status: 502, detail: null }), 'unavailable', true],
    [new ApiError('http', 'x', { status: 500, detail: 'boom' }), 'rejected', true],
    [new ApiError('http', 'x', { status: 422, detail: 'bad' }), 'rejected', false],
    [new ApiError('http', 'x', { status: 429, detail: 'slow down' }), 'rejected', true],
    [new Error('random'), 'unexpected', true],
  ] as const)('maps %o to %s', (error, category, retryable) => {
    const description = describeError(error, 'the data');
    expect(description.category).toBe(category);
    expect(description.retryable).toBe(retryable);
    expect(description.title.length).toBeGreaterThan(0);
  });

  it('never echoes server-provided detail text to the user', () => {
    const description = describeError(new ApiError('http', 'x', { status: 500, detail: '<script>secret</script>' }));
    expect(description.message).not.toContain('secret');
  });
});
