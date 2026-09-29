import { useCallback, useEffect, useRef, useState } from "react";
import apiService from "../services/api";
import type { Position } from "../services/types";

/** Open holdings, refreshed on demand (e.g. WebSocket alerts) and every minute. */
export function usePositions(pollMs = 60_000) {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (inFlight.current) return inFlight.current;
    const run = (async () => {
      try {
        setError(null);
        setPositions(await apiService.getPositions("open"));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load holdings");
      } finally {
        setLoading(false);
      }
    })();
    inFlight.current = run;
    try {
      await run;
    } finally {
      inFlight.current = null;
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, pollMs);
    return () => clearInterval(timer);
  }, [refresh, pollMs]);

  return { positions, loading, error, refresh };
}
