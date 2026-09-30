import { useResource } from "../../hooks/useResource";
import apiService from "../../services/api";
import PositionCard from "../Holdings/PositionCard";
import type { Position } from "../../services/types";
import ActionButtons from "./ActionButtons";
import TradeTable, { inr } from "./TradeTable";

interface TradePlanCardProps {
  symbol: string | null;
  refreshKey: number;
  /** Your open holding in this stock, if any. */
  position: Position | null;
  onPositionChanged: () => void;
  onOpenHolding: (position: Position) => void;
}

/** PRD §8: the trade plan for the selected stock, with reasoning, exit rules and Zerodha actions. */
export default function TradePlanCard({ symbol, refreshKey, position, onPositionChanged, onOpenHolding }: TradePlanCardProps) {
  const { data: plan, loading, error } = useResource(
    symbol ? () => apiService.getTradePlan(symbol) : null,
    [symbol, refreshKey],
    true
  );
  const current = plan && plan.symbol === symbol ? plan : null;

  return (
    <div className="glass-panel h-full flex flex-col p-4 gap-3 overflow-y-auto">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Trade Plan</h2>
        {current && (
          <span className="text-xs font-mono text-slate-500" title="Conviction score (0-100)">
            conviction {current.confidence_score.toFixed(0)}
          </span>
        )}
      </div>

      {!symbol && <div className="text-xs text-slate-500">Select a stock to see its trade plan.</div>}
      {symbol && loading && !current && <div className="text-xs text-slate-500">Building trade plan…</div>}
      {symbol && error && !current && <div className="text-xs text-neon-rose">Trade plan unavailable: {error}</div>}

      {current && (
        <>
          <div className="flex items-baseline justify-between gap-2">
            <div className="min-w-0">
              <div className="text-base font-semibold neon-text-blue truncate">{current.name}</div>
              <div className="text-[11px] text-slate-500">
                #{current.rank ?? "—"} overall · NSE:{current.symbol}
              </div>
            </div>
            {current.current_price !== null && (
              <div className="text-right shrink-0">
                <div className="text-sm font-mono text-slate-200">{inr(current.current_price)}</div>
                {current.change_pct !== null && (
                  <div className={`text-[11px] font-mono ${current.change_pct >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
                    {current.change_pct >= 0 ? "+" : ""}
                    {current.change_pct.toFixed(2)}%
                  </div>
                )}
              </div>
            )}
          </div>

          <PositionCard symbol={current.symbol} name={current.name} position={position} plan={current}
            onChanged={onPositionChanged} onOpen={onOpenHolding} />

          <TradeTable plan={current} />

          {current.market_regime === "risk-off" && current.action === "BUY" && (
            <div className="text-[11px] text-amber-300/90 bg-amber-300/5 border border-amber-300/20 rounded-lg px-3 py-2">
              Market regime is risk-off — consider half size.
            </div>
          )}

          <p className="text-xs text-slate-300 leading-relaxed">{current.reasoning}</p>
          {current.first_recommended_at && current.first_recommended_at.slice(0, 10) !== current.generated_at.slice(0, 10) && (
            <p className="text-[11px] text-slate-500">
              First recommended {new Date(current.first_recommended_at + (current.first_recommended_at.endsWith("Z") ? "" : "Z")).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
              {" "}— levels and reasons above are re-checked against today's price.
            </p>
          )}
          {current.ai_commentary && (
            <p className="text-xs text-slate-400 leading-relaxed border-l-2 border-neon-purple/40 pl-3">
              {current.first_recommended_at && current.first_recommended_at.slice(0, 10) !== current.generated_at.slice(0, 10) && (
                <span className="text-slate-500">AI note from when first recommended: </span>
              )}
              {current.ai_commentary}
            </p>
          )}

          {current.action !== "AVOID" && (
            <div className="text-[11px] text-slate-500 space-y-1">
              <p><span className="text-slate-400">Invalidation:</span> {current.invalidation}</p>
              <p><span className="text-slate-400">Exit:</span> {current.exit_logic}</p>
            </div>
          )}

          <div className="mt-auto pt-2">
            <ActionButtons symbol={current.symbol} canPrepareOrder={current.action === "BUY"} />
            <p className="text-[10px] text-slate-600 mt-2">
              Decision support only — you place and confirm every order yourself.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
