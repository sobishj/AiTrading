import { useState } from "react";
import apiService from "../../services/api";
import type { Position, TradePlan } from "../../services/types";
import PositionDialog, { PositionDialogMode } from "./PositionDialog";

interface PositionCardProps {
  symbol: string;
  name: string;
  position: Position | null;
  plan: TradePlan | null;
  onChanged: () => void;
  onOpen: (position: Position) => void;
}

const inr = (v: number, d = 2) => `₹${v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d })}`;

/** Trade Plan panel: your holding in this stock (with live risk) or an "I bought this" button. */
export default function PositionCard({ symbol, name, position, plan, onChanged, onOpen }: PositionCardProps) {
  const [dialog, setDialog] = useState<PositionDialogMode | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const close = () => setDialog(null);
  const done = () => {
    setDialog(null);
    onChanged();
  };

  const remove = async () => {
    if (!position) return;
    await apiService.deletePosition(position.id);
    setConfirmDelete(false);
    onChanged();
  };

  if (!position) {
    return (
      <>
        <button onClick={() => setDialog("buy")} className="btn-secondary w-full text-xs py-1.5" title="Record shares you bought so the AI monitors them">
          + I bought this — monitor it for me
        </button>
        {dialog && (
          <PositionDialog
            mode="buy"
            symbol={symbol}
            name={name}
            defaults={{
              price: plan?.current_price ?? null,
              stop_loss: plan ? plan.stop_loss : null,
              target: plan ? plan.target : null,
            }}
            onDone={done}
            onClose={close}
          />
        )}
      </>
    );
  }

  const risk = position.loss_risk ?? 0;
  const riskColor = risk >= 60 ? "bg-neon-rose" : risk >= 35 ? "bg-amber-300" : "bg-neon-emerald";
  const up = position.unrealized_pnl >= 0;

  return (
    <div className="glass-panel px-4 py-3 space-y-2 border-neon-purple/20">
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-widest text-neon-purple">Your holding</span>
        <span className={`text-sm font-mono font-semibold ${up ? "text-neon-emerald" : "text-neon-rose"}`}>
          {up ? "+" : "−"}{inr(Math.abs(position.unrealized_pnl), 0)}
          {position.unrealized_pct !== null && ` (${up ? "+" : ""}${position.unrealized_pct.toFixed(1)}%)`}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
        <span className="text-slate-500">Shares</span><span className="text-right font-mono">{position.quantity}</span>
        <span className="text-slate-500">Avg buy</span><span className="text-right font-mono">{inr(position.avg_price)}</span>
        <span className="text-slate-500">Your stop-loss</span>
        <span className="text-right font-mono text-neon-rose">{position.stop_loss ? inr(position.stop_loss) : "not set"}</span>
        <span className="text-slate-500">Your target</span>
        <span className="text-right font-mono text-neon-emerald">{position.target ? inr(position.target) : "not set"}</span>
        {position.realized_pnl !== 0 && (
          <>
            <span className="text-slate-500">Booked P&L</span>
            <span className={`text-right font-mono ${position.realized_pnl >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
              {inr(position.realized_pnl, 0)}
            </span>
          </>
        )}
      </div>
      <div title={position.risk_reasons.join("\n")}>
        <div className="flex justify-between text-[11px] text-slate-500 mb-0.5">
          <span>Risk of loss</span>
          <span>{position.loss_risk !== null ? `${risk.toFixed(0)}/100` : "checking…"}</span>
        </div>
        <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
          <div className={`h-full rounded-full ${riskColor}`} style={{ width: `${Math.max(3, risk)}%` }} />
        </div>
        {position.risk_reasons.length > 0 && (
          <ul className="mt-1 text-[11px] text-slate-400 list-disc pl-4 space-y-0.5">
            {position.risk_reasons.slice(0, 3).map((r) => <li key={r}>{r}</li>)}
          </ul>
        )}
      </div>
      {position.suggestion && position.suggestion.action !== "HOLD" && (
        <button onClick={() => onOpen(position)}
          className={`w-full text-left text-xs font-semibold rounded-lg px-2 py-1.5 border ${
            position.suggestion.action === "SELL" ? "border-neon-rose/40 bg-neon-rose/10 text-neon-rose" : "border-amber-300/40 bg-amber-300/10 text-amber-300"}`}>
          {position.suggestion.action === "SELL" ? "AI suggests: SELL" : "AI suggests: consider selling"} — open to mark it sold
        </button>
      )}
      <div className="flex gap-1.5 pt-1">
        <button onClick={() => onOpen(position)} className="btn-primary flex-1 text-xs py-1">Open holding</button>
        <button onClick={() => setDialog("sell")} className="btn-secondary flex-1 text-xs py-1">Record sale</button>
        <button onClick={() => setDialog("buy")} className="btn-secondary text-xs py-1 px-2" title="Bought more">+</button>
        <button onClick={() => setConfirmDelete(true)} className="btn-secondary text-xs py-1 px-2 hover:text-neon-rose" title="Delete this holding">✕</button>
      </div>
      {confirmDelete && (
        <div className="text-xs text-slate-300 bg-neon-rose/5 border border-neon-rose/20 rounded-lg p-2 space-y-1">
          <p>Delete this holding? Use this only for a mistaken entry — to record selling, use "Record sale".</p>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setConfirmDelete(false)} className="text-slate-400 hover:text-slate-200">Cancel</button>
            <button onClick={remove} className="text-neon-rose hover:underline">Delete</button>
          </div>
        </div>
      )}
      {position.last_checked_at && (
        <p className="text-[10px] text-slate-600">
          Monitored · last check {new Date(position.last_checked_at + "Z").toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
        </p>
      )}
      {dialog && (
        <PositionDialog
          mode={dialog}
          symbol={symbol}
          name={name}
          position={position}
          defaults={{ price: position.last_price, stop_loss: position.stop_loss, target: position.target }}
          onDone={done}
          onClose={close}
        />
      )}
    </div>
  );
}
