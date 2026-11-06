import { useCallback, useEffect, useEffectEvent, useState } from 'react';

export type ResourceState<T> =
  | { readonly status: 'idle' }
  | { readonly status: 'loading' }
  | { readonly status: 'success'; readonly data: T }
  | { readonly status: 'error'; readonly error: unknown };

type Settled<T> = { readonly key: string } & ({ readonly ok: true; readonly data: T } | { readonly ok: false; readonly error: unknown });

export interface Resource<T> {
  readonly state: ResourceState<T>;
  /** Re-run the loader for the current key (e.g. a "Try again" button). */
  readonly reload: () => void;
}

/**
 * Loads an async resource identified by `key`.
 *
 * - `key === null` means "do not load" (state `idle`).
 * - `key` must change whenever the loader's inputs change; stale responses are
 *   discarded and in-flight requests are aborted on key change and unmount.
 * - Loading is derived (the settled result belongs to a different key), so no
 *   synchronous state updates happen inside effects.
 */
export function useResource<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>): Resource<T> {
  const [attempt, setAttempt] = useState(0);
  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const requestKey = key === null ? null : `${key}::${attempt}`;
  const runLoad = useEffectEvent((signal: AbortSignal) => load(signal));

  useEffect(() => {
    if (requestKey === null) return;
    const controller = new AbortController();
    runLoad(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setSettled({ key: requestKey, ok: true, data });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setSettled({ key: requestKey, ok: false, error });
      },
    );
    return () => {
      controller.abort();
    };
  }, [requestKey]);

  const reload = useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);

  let state: ResourceState<T>;
  if (requestKey === null) state = { status: 'idle' };
  else if (settled?.key !== requestKey) state = { status: 'loading' };
  else state = settled.ok ? { status: 'success', data: settled.data } : { status: 'error', error: settled.error };

  return { state, reload };
}
