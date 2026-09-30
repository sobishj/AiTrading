import { useState } from "react";
import apiService from "../../services/api";
import type { TradeProposal } from "../../services/types";

interface TradeProposalCardProps {
  proposal: TradeProposal;
  onRecorded: () => void;
}

const input = "bg-base-800/80 border border-white/10 rounded-md px-2 py-1 text-xs text-slate-200 w-full";

/**
 * A trade you reported in chat, for you to check and confirm. Nothing is saved until you press Confirm;
 * then it is recorded exactly like the Holdings buttons (and AiTrading re-learns your trading style).
 */
export default function TradeProposalCard({ proposal, onRecorded }: TradeProposalCardProps) {
  const [symbol, setSymbol] = useState(proposal.symbol ?? "");
  const [rows, setRows] = useState(
    proposal.actions.map((a) => ({ side: a.side, quantity: a.quantity !== null ? String(a.quantity) : "",
      price: String(a.price), trade_date: a.trade_date, check: a.price_check }))
  );
  const [state, setState] = useState<"idle" | "saving" | "done" | "cancelled">("idle");
  const [error, setError] = useState<string | null>(null);

  const update = (i: number, key: "quantity" | "price" | "trade_date", value: string) =>
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, [key]: value } : r)));

  const valid = symbol.trim() !== "" && rows.every((r) => Number(r.quantity) > 0 && Number(r.price) > 0 && r.trade_date);

  const confirm = async () => {
    setState("saving");
    setError(null);
    try {
      await apiService.recordTrades(symbol.trim().toUpperCase(),
        rows.map((r) => ({ side: r.side, quantity: Number(r.quantity), price: Number(r.price), trade_date: r.trade_date })));
      setState("done");
      onRecorded();
    } catch (e) {
      setState("idle");
      setError(e instanceof Error ? e.message : "Could not record the trade");
    }
  };

  if (state === "done") {
    return <p className="text-[11px] text-neon-emerald mt-2">✓ Recorded in your holdings. Your trading style is being re-analysed.</p>;
  }
  if (state === "cancelled") {
    return <p className="text-[11px] text-slate-500 mt-2">Not recorded.</p>;
  }
  return (
    <div className="mt-2 rounded-xl border border-neon-blue/30 bg-neon-blue/5 p-3 space-y-2">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-slate-400">Stock</span>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="NSE symbol, e.g. ITC"
          className={`${input} w-28 font-mono uppercase`} />
        {proposal.name && symbol === proposal.symbol && <span className="text-slate-400 truncate">{proposal.name}</span>}
        {proposal.held_quantity !== null && symbol === proposal.symbol && (
          <span className="ml-auto text-[10px] text-slate-500">you hold {proposal.held_quantity}</span>
        )}
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[10px] text-slate-500 text-left">
            <th className="font-normal w-12"></th>
            <th className="font-normal">Quantity</th>
            <th className="font-normal">Price ₹</th>
            <th className="font-normal">Date</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className={`font-bold ${r.side === "BUY" ? "text-neon-emerald" : "text-neon-rose"}`}>{r.side}</td>
              <td className="pr-1"><input inputMode="decimal" value={r.quantity} onChange={(e) => update(i, "quantity", e.target.value)}
                placeholder="required" className={`${input} ${r.quantity ? "" : "border-amber-300/60"}`} /></td>
              <td className="pr-1">
                <input inputMode="decimal" value={r.price} onChange={(e) => update(i, "price", e.target.value)} className={input} />
                {r.check && <span className="block text-[9px] text-slate-600">day range {r.check.day_low}–{r.check.day_high}</span>}
              </td>
              <td><input type="date" value={r.trade_date} onChange={(e) => update(i, "trade_date", e.target.value)} className={input} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {proposal.warnings.map((w) => (
        <p key={w} className="text-[11px] text-amber-300">⚠ {w}</p>
      ))}
      {error && <p className="text-[11px] text-neon-rose">{error}</p>}
      <div className="flex gap-2 justify-end">
        <button onClick={() => setState("cancelled")} className="btn-secondary text-xs px-3 py-1">Cancel</button>
        <button onClick={confirm} disabled={!valid || state === "saving"} className="btn-primary text-xs px-3 py-1 disabled:opacity-40">
          {state === "saving" ? "Saving…" : "Confirm & record"}
        </button>
      </div>
    </div>
  );
}
