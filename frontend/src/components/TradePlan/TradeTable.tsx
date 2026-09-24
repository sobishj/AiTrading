import type { ReactNode } from "react";
import type { TradePlan } from "../../services/types";

interface TradeTableProps {
  plan: TradePlan;
}

const RISK_COLORS: Record<string, string> = {
  low: "text-neon-emerald bg-neon-emerald/10 border-neon-emerald/30",
  medium: "text-neon-blue bg-neon-blue/10 border-neon-blue/30",
  high: "text-neon-rose bg-neon-rose/10 border-neon-rose/30",
};

const ACTION_COLORS: Record<string, string> = {
  BUY: "text-neon-emerald bg-neon-emerald/10 border-neon-emerald/40 shadow-glow-emerald",
  WAIT: "text-amber-300 bg-amber-300/10 border-amber-300/30",
  AVOID: "text-neon-rose bg-neon-rose/10 border-neon-rose/30",
};

export const inr = (value: number, digits = 2) =>
  `₹${value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-white/5 last:border-0 gap-3">
      <span className="text-xs text-slate-500 shrink-0">{label}</span>
      <span className="text-sm font-mono font-medium text-slate-200 text-right">{value}</span>
    </div>
  );
}

/** PRD §8 fields: Recommendation, Instrument, Entry, Target, Stop-loss, Holding, Risk, Risk/Reward. */
export default function TradeTable({ plan }: TradeTableProps) {
  const riskClass = RISK_COLORS[plan.risk_level] ?? RISK_COLORS.medium;
  const actionClass = ACTION_COLORS[plan.action] ?? ACTION_COLORS.WAIT;
  const inactive = plan.action === "AVOID";

  return (
    <div className="glass-panel px-4 py-2">
      <Row label="Recommendation" value={<span className={`pill border font-sans ${actionClass}`}>{plan.action}</span>} />
      <Row label="Instrument" value={<span className="font-sans text-xs">{plan.instrument}</span>} />
      <Row label="Setup" value={<span className="font-sans text-xs text-neon-purple">{plan.strategy}</span>} />
      <Row
        label="Entry"
        value={
          <span className={inactive ? "text-slate-500" : ""}>
            {inr(plan.entry_low)} – {plan.entry_high.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        }
      />
      <Row label="Target" value={<span className={inactive ? "text-slate-500" : "text-neon-emerald"}>{inr(plan.target)}</span>} />
      <Row label="Stop-loss" value={<span className={inactive ? "text-slate-500" : "text-neon-rose"}>{inr(plan.stop_loss)}</span>} />
      <Row label="Holding" value={<span className="font-sans text-xs">{plan.holding_period}</span>} />
      <Row label="Risk" value={<span className={`pill border font-sans ${riskClass}`}>{plan.risk_level.toUpperCase()}</span>} />
      <Row label="Risk / Reward" value={`1 : ${plan.risk_reward.toFixed(1)}`} />
      {plan.action !== "AVOID" && plan.quantity > 0 && (
        <Row
          label="Position size"
          value={
            <span title={`${plan.risk_per_trade_pct}% of ${inr(plan.capital, 0)} at risk`}>
              {plan.quantity} sh · {inr(plan.capital_required, 0)}
            </span>
          }
        />
      )}
    </div>
  );
}
