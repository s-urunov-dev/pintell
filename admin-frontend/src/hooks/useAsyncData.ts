import { useCallback, useEffect, useRef, useState } from 'react';

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  /**
   * The rejection itself, not a message. Failures are turned into text at
   * render time so that switching language re-translates an error that is
   * already on screen.
   */
  error: unknown;
  reload: () => void;
}

/**
 * Runs an async loader whenever `deps` change, cancelling the in-flight
 * request so a slow earlier response can never overwrite a newer one.
 */
export function useAsyncData<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    setLoading(true);
    setError(null);

    loaderRef
      .current(controller.signal)
      .then((result) => {
        if (active) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!active || controller.signal.aborted) return;
        setError(err ?? new Error('unknown'));
        setLoading(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadToken]);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  return { data, loading, error, reload };
}
