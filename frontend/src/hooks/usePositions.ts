import { useCallback, useEffect, useRef, useState } from "react";
import apiService from "../services/api";
import type { Position } from "../services/types";

/**
 * Holdings, refreshed on demand (e.g. WebSocket alerts) and every minute.
 * `positions` = open holdings; `sold` = every holding with a recorded sale (closed or partly sold).
 */
export function usePositions(pollMs = 60_000) {
  const [positions, setPositions] = useState<Position[]>([]);
  const [sold, setSold] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (inFlight.current) return inFlight.current;
    const run = (async () => {
      try {
        setError(null);
        const all = await apiService.getPositions("all");
        setPositions(all.filter((p) => p.status === "open"));
        setSold(
          all
            .filter((p) => (p.sold_quantity ?? 0) > 0)
            .sort((a, b) => (b.last_sold_on ?? "").localeCompare(a.last_sold_on ?? ""))
        );
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

  return { positions, sold, loading, error, refresh };
}
