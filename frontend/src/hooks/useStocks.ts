import { useCallback, useEffect, useRef, useState } from "react";
import apiService from "../services/api";
import type { ListMode, Stock } from "../services/types";

interface UseStocksResult {
  stocks: Stock[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** One live-ranked list (Auto or Manual). Refreshed on WebSocket ranking updates, with optional polling. */
export function useStocks(list: ListMode = "auto", pollIntervalMs = 0): UseStocksResult {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);
  const rerun = useRef(false);

  const refresh = useCallback(async (): Promise<void> => {
    // A refresh requested while one is running (e.g. a WebSocket update racing a
    // user add/remove) must not be dropped: queue exactly one follow-up fetch.
    if (inFlight.current) {
      rerun.current = true;
      return inFlight.current;
    }
    const run = (async () => {
      do {
        rerun.current = false;
        try {
          setError(null);
          setStocks(await apiService.getStocks(list));
        } catch (err) {
          setError(err instanceof Error ? err.message : "Failed to load stocks");
        }
      } while (rerun.current);
    })();
    inFlight.current = run;
    try {
      await run;
    } finally {
      inFlight.current = null;
      setLoading(false);
    }
  }, [list]);

  useEffect(() => {
    refresh();
    if (pollIntervalMs > 0) {
      const interval = setInterval(refresh, pollIntervalMs);
      return () => clearInterval(interval);
    }
  }, [refresh, pollIntervalMs]);

  return { stocks, loading, error, refresh };
}
