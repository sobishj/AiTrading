import { DependencyList, useCallback, useEffect, useRef, useState } from "react";

interface UseResourceResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Fetch-on-change helper shared by the per-stock panels. Refetches whenever
 * `deps` change; stale responses from a previous symbol are discarded. When
 * `fetcher` is null (e.g. nothing selected) the data is cleared.
 *
 * `keepPrevious` keeps showing the last data while a background refresh
 * (e.g. a WebSocket-triggered re-rank) is in flight, instead of flashing a
 * loading state.
 */
export function useResource<T>(
  fetcher: (() => Promise<T>) | null,
  deps: DependencyList,
  keepPrevious = false
): UseResourceResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const requestId = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const run = fetcherRef.current;
    const id = ++requestId.current;
    if (!run) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }
    if (!keepPrevious) setData(null);
    setLoading(true);
    setError(null);
    run()
      .then((result) => {
        if (id === requestId.current) setData(result);
      })
      .catch((err: unknown) => {
        if (id === requestId.current) setError(err instanceof Error ? err.message : "Request failed");
      })
      .finally(() => {
        if (id === requestId.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, loading, error, reload };
}
