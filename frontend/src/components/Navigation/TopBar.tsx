import { FormEvent, useState } from "react";
import type { Theme } from "../../hooks/useTheme";
import type { LayoutPreset } from "../../layouts/MainLayout";
import type { AppStatus, Stock } from "../../services/types";
import AlertsBell from "./AlertsBell";
import RefreshIntervalControl from "./RefreshIntervalControl";

interface TopBarProps {
  stocks: Stock[];
  status: AppStatus | null;
  connected: boolean;
  refreshing: boolean;
  onSelect: (symbol: string) => void;
  onAddStock: (symbol: string) => Promise<void>;
  onRefresh: () => void;
  onOpenLearning: () => void;
  /** Settings window (data sources, general options, backup & restore). */
  onOpenSettings: () => void;
  alertsKey: number;
  onAlertSelect: (symbol: string) => void;
  theme: Theme;
  onToggleTheme: () => void;
  onLayoutPreset: (preset: LayoutPreset) => void;
}

const LAYOUT_OPTIONS: { value: LayoutPreset; label: string }[] = [
  { value: "balanced", label: "Balanced (reset)" },
  { value: "chat", label: "Focus chat" },
  { value: "chart", label: "Focus chart" },
  { value: "analysis", label: "Focus analysis" },
];

function ThemeSwitch({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const light = theme === "light";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={light}
      aria-label="Light theme"
      title={light ? "Switch to dark theme" : "Switch to light theme"}
      onClick={onToggle}
      className="flex items-center gap-2 text-[11px] text-slate-400 shrink-0"
    >
      <span aria-hidden>☾</span>
      <span
        className={`relative inline-flex h-5 w-9 items-center rounded-full border transition-colors duration-200 ${
          light ? "bg-neon-blue/80 border-neon-blue/60" : "bg-base-700 border-white/10"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 rounded-full bg-onaccent shadow transition-transform duration-200 ${
            light ? "translate-x-[18px]" : "translate-x-[2px]"
          }`}
        />
      </span>
      <span aria-hidden>☀</span>
    </button>
  );
}

function StatusDot({ ok, label, title }: { ok: boolean; label: string; title: string }) {
  return (
    <span title={title} className="flex items-center gap-1.5 text-[11px] text-slate-400">
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? "bg-neon-emerald pulse-glow" : "bg-neon-rose"}`} />
      {label}
    </span>
  );
}

/** PRD §5 top bar: brand, live status, search (jump to any share, or add an NSE symbol to your list), refresh, learning. */
export default function TopBar({
  stocks, status, connected, refreshing, onSelect, onAddStock, onRefresh, onOpenLearning,
  theme, onToggleTheme, onLayoutPreset, onOpenSettings, alertsKey, onAlertSelect,
}: TopBarProps) {
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const q = query.trim().toUpperCase();
  const matches = q
    ? stocks.filter((s) => s.symbol.includes(q) || s.name.toUpperCase().includes(q)).slice(0, 6)
    : [];
  const canAdd = q.length > 0 && /^[A-Z0-9&-]{1,20}$/.test(q) && !stocks.some((s) => s.symbol === q);

  const update = (value: string) => {
    setQuery(value);
    setAddError(null);
  };

  const choose = (symbol: string) => {
    onSelect(symbol);
    update("");
  };

  const add = async () => {
    setAdding(true);
    setAddError(null);
    try {
      await onAddStock(q);
      update("");
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Could not add symbol");
    } finally {
      setAdding(false);
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (matches.length > 0) choose(matches[0].symbol);
    else if (canAdd) add();
  };

  const lastRanked = status?.last_ranked_at ? new Date(status.last_ranked_at + "Z") : null;

  return (
    <header className="h-14 flex items-center justify-between gap-4 px-5 glass-panel rounded-none border-x-0 border-t-0 relative z-40">
      <div className="flex items-center gap-3 shrink-0">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-neon-blue to-neon-purple shadow-glow flex items-center justify-center font-bold text-sm">
          A
        </div>
        <span className="font-semibold text-lg tracking-tight">
          Ai<span className="neon-text-blue">Trading</span>
        </span>
        <div className="hidden lg:flex items-center gap-3 ml-3">
          <StatusDot ok={connected} label={connected ? "Live" : "Offline"} title="Real-time updates connection" />
          {status && (
            <>
              <StatusDot
                ok
                label={status.data_source === "kite" ? "Kite data" : "Yahoo data"}
                title={status.data_source === "kite" ? "Market data from Zerodha Kite" : "Market data from Yahoo Finance (may be ~15 min delayed). Log in to Kite at /kite/login for broker data."}
              />
              <StatusDot
                ok={status.llm_available}
                label={status.llm_available ? "AI online" : "AI offline"}
                title={`Local LLM ${status.llm_model} ${status.llm_available ? "reachable" : "not reachable — rankings still work"}`}
              />
            </>
          )}
          {lastRanked && (
            <span className="text-[11px] text-slate-500" title="Last full refresh">
              ranked {lastRanked.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
        </div>
      </div>

      <form onSubmit={handleSubmit} className="relative w-full max-w-md">
        <input
          value={query}
          onChange={(e) => update(e.target.value)}
          placeholder="Find a share, or type an NSE symbol to add to your list…"
          className="w-full bg-base-800/80 border border-white/10 rounded-xl px-3.5 py-2 text-sm
            placeholder:text-slate-500 focus:outline-none focus:border-neon-blue/50 focus:shadow-glow
            transition-all duration-150"
        />
        {q && (matches.length > 0 || canAdd || addError) && (
          <div className="absolute top-full mt-1 left-0 right-0 glass-panel bg-base-900/95 p-1 shadow-xl">
            {matches.map((s) => (
              <button
                key={s.symbol}
                type="button"
                onClick={() => choose(s.symbol)}
                className="w-full text-left px-3 py-1.5 rounded-lg text-sm hover:bg-white/5 flex justify-between"
              >
                <span>{s.name}</span>
                <span className="text-[11px] text-slate-500 font-mono">{s.symbol}</span>
              </button>
            ))}
            {canAdd && (
              <button
                type="button"
                onClick={add}
                disabled={adding}
                className="w-full text-left px-3 py-1.5 rounded-lg text-sm text-neon-blue hover:bg-white/5 disabled:opacity-50"
              >
                {adding ? `Adding ${q}…` : `+ Add ${q} to my list`}
              </button>
            )}
            {addError && <div className="px-3 py-1.5 text-xs text-neon-rose">{addError}</div>}
          </div>
        )}
      </form>

      <div className="flex items-center gap-3 shrink-0">
        <RefreshIntervalControl />
        <button onClick={onRefresh} disabled={refreshing} className="btn-secondary px-3 py-1.5 text-xs disabled:opacity-50" title="Refresh prices and rankings now">
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
        <AlertsBell refreshKey={alertsKey} onSelect={onAlertSelect} />
        <button onClick={onOpenSettings} className="btn-secondary px-3 py-1.5 text-xs"
          title={"Settings: AI models, data sources, general options, backup & restore" + (status?.models
            ? `\nChat AI: ${status.models.chat?.name} (${status.models.chat?.model})\nBackground AI: ${status.models.background?.name} (${status.models.background?.model})`
            : "")}>
          Settings
        </button>
        <button onClick={onOpenLearning} className="btn-secondary px-3 py-1.5 text-xs" title="Upload Zerodha trades, strategy performance, risk settings">
          Learning
        </button>
        <select
          value=""
          onChange={(e) => {
            if (e.target.value) onLayoutPreset(e.target.value as LayoutPreset);
          }}
          title="Panel layout — you can also drag the dividers between panels"
          className="bg-base-800/80 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-200
            focus:outline-none focus:border-neon-blue/50"
        >
          <option value="" disabled>
            Layout
          </option>
          {LAYOUT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <ThemeSwitch theme={theme} onToggle={onToggleTheme} />
      </div>
    </header>
  );
}
