import { useEffect, useState } from "react";
import type { Position } from "../../services/types";

interface HoldingsListProps {
  positions: Position[];
  /** Holdings with a recorded sale (closed or partly sold). */
  sold: Position[];
  loading: boolean;
  error: string | null;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
  onAdd: () => void;
  /** Opens the holding window (details, edit, AI suggestion, mark as sold). */
  onOpen: (position: Position) => void;
}

const inr0 = (v: number) => `${v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

function riskTone(risk: number | null) {
  if (risk === null) return "bg-slate-600";
  if (risk >= 60) return "bg-neon-rose pulse-glow";
  if (risk >= 35) return "bg-amber-300";
  return "bg-neon-emerald";
}

/** Holdings tab: what you own, live P&L, and a risk dot (green / amber / red). */
const VIEW_KEY = "aitrading.holdingsView";

function readView(): "open" | "sold" {
  try {
    return localStorage.getItem(VIEW_KEY) === "sold" ? "sold" : "open";
  } catch {
    return "open";
  }
}

export default function HoldingsList({ positions, sold, loading, error, selectedSymbol, onSelect, onAdd, onOpen }: HoldingsListProps) {
  const [view, setView] = useState<"open" | "sold">(readView);
  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* per-browser convenience only */
    }
  }, [view]);
  const invested = positions.reduce((sum, p) => sum + p.invested, 0);
  const pnl = positions.reduce((sum, p) => sum + p.unrealized_pnl, 0);
  const booked = sold.reduce((sum, p) => sum + p.realized_pnl, 0);
  const winners = sold.filter((p) => p.realized_pnl > 0).length;

  const toggle = (
    <div className="flex rounded-lg bg-white/5 p-0.5 text-[11px]">
      {(["open", "sold"] as const).map((v) => (
        <button key={v} onClick={() => setView(v)}
          className={`flex-1 py-1 rounded-md transition-colors ${view === v ? "bg-neon-blue/20 text-neon-blue" : "text-slate-400 hover:text-slate-200"}`}>
          {v === "open" ? `Open (${positions.length})` : `Sold (${sold.length})`}
        </button>
      ))}
    </div>
  );

  if (view === "sold") {
    return (
      <div className="flex flex-col h-full min-h-0">
        <div className="px-3 pb-2 space-y-2 border-b border-white/5">
          {toggle}
          {sold.length > 0 && (
            <div className="flex justify-between text-[11px] text-slate-400">
              <span>{winners} of {sold.length} in profit</span>
              <span className={booked >= 0 ? "text-neon-emerald" : "text-neon-rose"}>
                Booked {booked >= 0 ? "+" : ""}{inr0(booked)}
              </span>
            </div>
          )}
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {!loading && sold.length === 0 && (
            <div className="text-xs text-slate-500 px-3 py-4 text-center leading-relaxed">
              No sales yet. When you mark shares as sold, the trade and its profit or loss appear here.
            </div>
          )}
          {sold.map((p) => {
            const selected = p.symbol === selectedSymbol;
            const up = p.realized_pnl >= 0;
            return (
              <button
                key={p.id}
                onClick={() => {
                  onSelect(p.symbol);
                  onOpen(p);
                }}
                title="Open the trade — see every buy and sale, edit or undo them"
                className={`w-full text-left px-3 py-2 rounded-xl transition-all duration-200 ${
                  selected ? "bg-gradient-to-r from-neon-blue/15 to-neon-purple/10 border border-neon-blue/30"
                    : "border border-transparent hover:bg-white/5 hover:border-white/10"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={`flex-1 truncate text-sm font-medium ${selected ? "text-neon-blue" : "text-slate-200"}`}>{p.name}</span>
                  {p.status === "open" && (
                    <span className="text-[10px] px-1.5 rounded bg-white/10 text-slate-400" title={`${p.quantity} still held`}>part sold</span>
                  )}
                  <span className={`text-xs font-mono ${up ? "text-neon-emerald" : "text-neon-rose"}`}>
                    {up ? "+" : ""}{inr0(p.realized_pnl)}
                  </span>
                </div>
                <div className="flex justify-between text-[11px] mt-0.5">
                  <span className="text-slate-500">
                    {p.sold_quantity} · ₹{p.avg_buy_price_sold?.toLocaleString("en-IN")} → ₹{p.avg_sell_price?.toLocaleString("en-IN")}
                  </span>
                  <span className={up ? "text-neon-emerald" : "text-neon-rose"}>
                    {p.realized_pct !== null && p.realized_pct !== undefined ? `${p.realized_pct >= 0 ? "+" : ""}${p.realized_pct.toFixed(1)}%` : "—"}
                  </span>
                </div>
                <div className="text-[10px] text-slate-600 mt-0.5">
                  Bought {p.opened_on} · sold {p.last_sold_on}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-3 pb-2 space-y-2 border-b border-white/5">
        {toggle}
        <button onClick={onAdd} className="btn-primary w-full text-xs py-1.5">+ Add shares I bought</button>
        {positions.length > 0 && (
          <div className="flex justify-between text-[11px] text-slate-400">
            <span>Invested {inr0(invested)}</span>
            <span className={pnl >= 0 ? "text-neon-emerald" : "text-neon-rose"}>
              P&L {pnl >= 0 ? "+" : ""}{inr0(pnl)}
            </span>
          </div>
        )}
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {loading && positions.length === 0 && <div className="text-xs text-slate-500 px-3 py-4 text-center">Loading holdings…</div>}
        {error && <div className="text-xs text-neon-rose px-3 py-2">{error}</div>}
        {!loading && positions.length === 0 && (
          <div className="text-xs text-slate-500 px-3 py-4 text-center leading-relaxed">
            No holdings yet. Add the shares you bought and the AI will watch them — it alerts you near your stop-loss,
            at your target, and when the risk of a loss rises.
          </div>
        )}
        {positions.map((p) => {
          const selected = p.symbol === selectedSymbol;
          return (
            <button
              key={p.id}
              onClick={() => {
                onSelect(p.symbol);
                onOpen(p);
              }}
              title={p.risk_reasons.length ? `Loss risk ${p.loss_risk?.toFixed(0)}/100: ${p.risk_reasons.join("; ")}` : undefined}
              className={`w-full text-left px-3 py-2 rounded-xl transition-all duration-200 ${
                selected
                  ? "bg-gradient-to-r from-neon-blue/15 to-neon-purple/10 border border-neon-blue/30 shadow-glow"
                  : "border border-transparent hover:bg-white/5 hover:border-white/10"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full shrink-0 ${riskTone(p.loss_risk)}`} />
                <span className={`flex-1 truncate text-sm font-medium ${selected ? "text-neon-blue" : "text-slate-200"}`}>{p.name}</span>
                {p.suggestion?.action === "SELL" && (
                  <span className="text-[10px] font-bold px-1.5 rounded bg-neon-rose/20 text-neon-rose" title={p.suggestion.reason}>SELL</span>
                )}
                {p.suggestion?.action === "CONSIDER_SELLING" && (
                  <span className="text-[10px] font-bold px-1.5 rounded bg-amber-300/20 text-amber-300" title={p.suggestion.reason}>WATCH</span>
                )}
                {p.unread_alerts > 0 && <span className="text-[10px] px-1.5 rounded-full bg-neon-rose/20 text-neon-rose">{p.unread_alerts}</span>}
              </div>
              <div className="flex justify-between text-[11px] mt-0.5 pl-4">
                <span className="text-slate-500">{p.quantity} @ ₹{p.avg_price.toLocaleString("en-IN")}</span>
                <span className={p.unrealized_pnl >= 0 ? "text-neon-emerald" : "text-neon-rose"}>
                  {p.unrealized_pct !== null ? `${p.unrealized_pct >= 0 ? "+" : ""}${p.unrealized_pct.toFixed(1)}%` : "—"}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
