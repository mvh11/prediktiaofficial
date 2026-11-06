import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useResource } from './useResource.ts';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('useResource', () => {
  it('is idle and does not load when the key is null', () => {
    const load = vi.fn(() => Promise.resolve(1));
    const { result } = renderHook(() => useResource(null, load));
    expect(result.current.state).toEqual({ status: 'idle' });
    expect(load).not.toHaveBeenCalled();
  });

  it('goes from loading to success', async () => {
    const { result } = renderHook(() => useResource('k', () => Promise.resolve('data')));
    expect(result.current.state.status).toBe('loading');
    await waitFor(() => {
      expect(result.current.state).toEqual({ status: 'success', data: 'data' });
    });
  });

  it('reports errors and recovers on reload', async () => {
    const load = vi.fn<(signal: AbortSignal) => Promise<string>>().mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce('ok');
    const { result } = renderHook(() => useResource('k', load));
    await waitFor(() => {
      expect(result.current.state.status).toBe('error');
    });
    act(() => {
      result.current.reload();
    });
    expect(result.current.state.status).toBe('loading');
    await waitFor(() => {
      expect(result.current.state).toEqual({ status: 'success', data: 'ok' });
    });
  });

  it('aborts the previous request and ignores its late result when the key changes', async () => {
    const first = deferred<string>();
    const signals: AbortSignal[] = [];
    const { result, rerender } = renderHook(({ k }) => useResource(k, (signal) => {
      signals.push(signal);
      return k === 'a' ? first.promise : Promise.resolve('b-result');
    }), { initialProps: { k: 'a' } });

    rerender({ k: 'b' });
    await waitFor(() => {
      expect(result.current.state).toEqual({ status: 'success', data: 'b-result' });
    });
    expect(signals[0]?.aborted).toBe(true);

    first.resolve('stale a-result');
    await Promise.resolve();
    expect(result.current.state).toEqual({ status: 'success', data: 'b-result' });
  });

  it('aborts in-flight work on unmount', () => {
    let captured: AbortSignal | undefined;
    const { unmount } = renderHook(() =>
      useResource('k', (signal) => {
        captured = signal;
        return new Promise<never>(() => undefined);
      }),
    );
    unmount();
    expect(captured?.aborted).toBe(true);
  });
});
