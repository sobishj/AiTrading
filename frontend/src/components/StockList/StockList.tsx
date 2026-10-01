import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import type { ListMode, Stock } from "../../services/types";
import DiscoveryBar from "./DiscoveryBar";
import StockListItem from "./StockListItem";

interface StockListProps {
  mode: ListMode;
  onModeChange: (mode: ListMode) => void;
  stocks: Stock[];
  /** Every stock the app knows (for "add to my list" suggestions by name). */
  knownStocks: Stock[];
  loading: boolean;
  error: string | null;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
  onAdd: (symbol: string) => Promise<void>;
  onRemove: (symbol: string) => Promise<void>;
  /** Rendered in the Holdings tab (your positions, monitored by the AI). */
  holdingsView: ReactNode;
  holdingsCount: number;
}

const MOVE_HIGHLIGHT_MS = 6000;
const SYMBOL_RE = /^[A-Z0-9&-]{1,20}$/;

/**
 * PRD §6 left panel: stock names only, strongest opportunity first.
 * - Auto: the best of the day, picked each weekday from the whole NIFTY 500 (real-data screen,
 *   then AI review — see DiscoveryBar), and re-ranked live by the engine.
 * - Manual: the user's own list; shares can be added and removed here, and
 *   each gets the same chart, trade plan, analysis and chat.
 * The search box filters the list in both modes; in Manual mode it also
 * offers to add matching shares (or any NSE symbol).
 */
export default function StockList({
  mode, onModeChange, stocks, knownStocks, loading, error, selectedSymbol, onSelect, onAdd, onRemove,
  holdingsView, holdingsCount,
}: StockListProps) {
  const previousRanks = useRef<Map<string, number>>(new Map());
  const [moves, setMoves] = useState<Record<string, "up" | "down">>({});
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    setQuery("");
    setActionError(null);
    previousRanks.current = new Map();
  }, [mode]);

  useEffect(() => {
    const next: Record<string, "up" | "down"> = {};
    const seen = previousRanks.current;
    for (const stock of stocks) {
      const before = seen.get(stock.symbol);
      if (before !== undefined && stock.list_rank !== null && before !== stock.list_rank) {
        next[stock.symbol] = stock.list_rank < before ? "up" : "down";
      }
    }
    previousRanks.current = new Map(
      stocks.filter((s) => s.list_rank !== null).map((s) => [s.symbol, s.list_rank as number])
    );
    if (Object.keys(next).length === 0) return;
    setMoves(next);
    const timer = setTimeout(() => setMoves({}), MOVE_HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [stocks]);

  // Re-read how the list was picked whenever its membership changes (not on every re-rank).
  const membership = useMemo(() => stocks.map((s) => s.symbol).sort().join(","), [stocks]);

  const q = query.trim().toUpperCase();
  const filtered = useMemo(() => {
    if (!q) return stocks;
    return stocks.filter((s) => s.symbol.toUpperCase().includes(q) || s.name.toUpperCase().includes(q));
  }, [stocks, q]);

  // Manual mode: shares matching the search that aren't on the list yet.
  const addSuggestions = useMemo(() => {
    if (mode !== "manual" || !q) return [];
    const inList = new Set(stocks.map((s) => s.symbol));
    const byName = knownStocks
      .filter((s) => !inList.has(s.symbol) && (s.symbol.includes(q) || s.name.toUpperCase().includes(q)))
      .slice(0, 5)
      .map((s) => ({ symbol: s.symbol, label: s.name }));
    const exact = byName.some((s) => s.symbol === q) || inList.has(q);
    if (!exact && SYMBOL_RE.test(q)) byName.push({ symbol: q, label: `${q} (NSE symbol)` });
    return byName;
  }, [mode, q, stocks, knownStocks]);

  const add = async (symbol: string) => {
    setAdding(symbol);
    setActionError(null);
    try {
      await onAdd(symbol);
      setQuery("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : `Could not add ${symbol}`);
    } finally {
      setAdding(null);
    }
  };

  const remove = async (symbol: string) => {
    setActionError(null);
    try {
      await onRemove(symbol);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : `Could not remove ${symbol}`);
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (filtered.length > 0) onSelect(filtered[0].symbol);
    else if (addSuggestions.length > 0) add(addSuggestions[0].symbol);
  };

  return (
    <div className="glass-panel h-full flex flex-col overflow-hidden">
      <div className="px-3 pt-3 pb-2 border-b border-white/5 space-y-2">
        <div className="flex items-center gap-1 bg-base-800/70 rounded-lg p-0.5 border border-white/5" role="tablist">
          {(["auto", "manual", "holdings"] as ListMode[]).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              onClick={() => onModeChange(m)}
              title={m === "auto" ? "Today's best shares, picked from the whole NIFTY 500 by real data and AI review" : m === "manual" ? "Your own list — add and remove shares"
                : "Shares you bought — monitored for stop-loss, target and loss risk"}
              className={`flex-1 px-2 py-1 rounded-md text-xs font-medium transition-all duration-150 ${
                mode === m ? "bg-neon-blue/15 text-neon-blue shadow-glow" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {m === "auto" ? "Auto" : m === "manual" ? "Manual" : `Holdings${holdingsCount ? ` ${holdingsCount}` : ""}`}
            </button>
          ))}
        </div>

        {mode !== "holdings" && (
        <form onSubmit={handleSubmit}>
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActionError(null);
            }}
            placeholder={mode === "manual" ? "Search or add a share…" : "Search this list…"}
            aria-label={mode === "manual" ? "Search or add a share" : "Search the list"}
            className="w-full bg-base-800/80 border border-white/10 rounded-lg px-3 py-1.5 text-xs
              placeholder:text-slate-500 focus:outline-none focus:border-neon-blue/50 transition-all duration-150"
          />
        </form>
        )}

        {mode === "auto" && <DiscoveryBar refreshKey={membership} />}

        {addSuggestions.length > 0 && (
          <div className="space-y-0.5">
            {addSuggestions.map((s) => (
              <button
                key={s.symbol}
                onClick={() => add(s.symbol)}
                disabled={adding !== null}
                className="w-full text-left px-2 py-1 rounded-md text-xs text-neon-blue hover:bg-white/5 disabled:opacity-50 truncate"
              >
                {adding === s.symbol ? `Adding ${s.label}…` : `+ Add ${s.label}`}
              </button>
            ))}
          </div>
        )}
        {actionError && <p className="text-[11px] text-neon-rose">{actionError}</p>}
      </div>

      {mode === "holdings" ? (
        <div className="flex-1 min-h-0 pt-2">{holdingsView}</div>
      ) : (
      <>
      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {loading && stocks.length === 0 && (
          <div className="text-xs text-slate-500 px-3 py-4 text-center">Ranking the market…</div>
        )}
        {error && stocks.length === 0 && (
          <div className="text-xs text-neon-rose px-3 py-4 text-center">Can't reach the backend: {error}</div>
        )}
        {!loading && !error && stocks.length === 0 && mode === "manual" && (
          <div className="text-xs text-slate-500 px-3 py-4 text-center leading-relaxed">
            Your list is empty. Type a company name or NSE symbol above to add a share — it gets the same
            chart, trade plan and AI analysis.
          </div>
        )}
        {!loading && !error && stocks.length === 0 && mode === "auto" && (
          <div className="text-xs text-slate-500 px-3 py-4 text-center">No shares picked yet — today's market-wide pick will fill this list.</div>
        )}
        {q && stocks.length > 0 && filtered.length === 0 && addSuggestions.length === 0 && (
          <div className="text-xs text-slate-500 px-3 py-4 text-center">No shares match “{query.trim()}”.</div>
        )}
        {filtered.map((stock) => (
          <StockListItem
            key={stock.symbol}
            stock={stock}
            selected={stock.symbol === selectedSymbol}
            moved={moves[stock.symbol]}
            onSelect={onSelect}
            onRemove={mode === "manual" ? remove : undefined}
          />
        ))}
      </div>

      <div className="px-4 py-1.5 border-t border-white/5 text-[10px] text-slate-600 flex justify-between">
        <span>{mode === "auto" ? "Best of the day" : "Your list"}</span>
        <span>
          {filtered.length}
          {q ? ` of ${stocks.length}` : ""} · best first
        </span>
      </div>
      </>
      )}
    </div>
  );
}
