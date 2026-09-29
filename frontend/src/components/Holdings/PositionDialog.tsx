import { FormEvent, useMemo, useState } from "react";
import apiService from "../../services/api";
import type { Position, Stock } from "../../services/types";

export type PositionDialogMode = "buy" | "sell" | "edit";

interface PositionDialogProps {
  mode: PositionDialogMode;
  /** Stock being bought (buy mode); omit to let the user pick one. */
  symbol?: string | null;
  name?: string | null;
  position?: Position | null;
  /** Prefill for buy mode: current price and the AI plan's levels. */
  defaults?: { price?: number | null; stop_loss?: number | null; target?: number | null };
  knownStocks?: Stock[];
  onDone: (position: Position | null) => void;
  onClose: () => void;
}

const today = () => new Date().toISOString().slice(0, 10);
const num = (v: string) => (v.trim() === "" ? null : Number(v));

/** One dialog for recording a buy, a sell, or changing stop-loss / target. */
export default function PositionDialog({
  mode, symbol, name, position, defaults, knownStocks = [], onDone, onClose,
}: PositionDialogProps) {
  const [pick, setPick] = useState(symbol ?? "");
  const [quantity, setQuantity] = useState(mode === "sell" && position ? String(position.quantity) : "");
  const [price, setPrice] = useState(
    mode === "sell" ? String(position?.last_price ?? "") : defaults?.price ? String(Number(defaults.price.toFixed(2))) : ""
  );
  const [tradeDate, setTradeDate] = useState(today());
  const [stop, setStop] = useState(String(position?.stop_loss ?? defaults?.stop_loss ?? ""));
  const [target, setTarget] = useState(String(position?.target ?? defaults?.target ?? ""));
  const [notes, setNotes] = useState(position?.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const suggestions = useMemo(() => {
    const q = pick.trim().toUpperCase();
    if (mode !== "buy" || symbol || !q) return [];
    return knownStocks.filter((s) => s.symbol.includes(q) || s.name.toUpperCase().includes(q)).slice(0, 5);
  }, [pick, knownStocks, mode, symbol]);

  const title = mode === "buy" ? `I bought ${name ?? "shares"}` : mode === "sell" ? `Sell ${position?.name}` : `Stop-loss & target — ${position?.name}`;
  const qtyNum = Number(quantity);
  const priceNum = Number(price);
  const pnlPreview =
    mode === "sell" && position && qtyNum > 0 && priceNum > 0 ? (priceNum - position.avg_price) * qtyNum : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      let result: Position | null = null;
      if (mode === "buy") {
        const sym = (symbol ?? pick).trim().toUpperCase();
        if (!sym) throw new Error("Choose a stock");
        if (!(qtyNum > 0)) throw new Error("Enter how many shares you bought");
        result = await apiService.buyShares({
          symbol: sym, quantity: qtyNum, price: num(price), trade_date: tradeDate,
          stop_loss: num(stop), target: num(target), notes: notes || null,
        });
      } else if (mode === "sell" && position) {
        if (!(qtyNum > 0) || !(priceNum > 0)) throw new Error("Enter the quantity and the price you sold at");
        result = await apiService.sellShares(position.id, { quantity: qtyNum, price: priceNum, trade_date: tradeDate });
      } else if (mode === "edit" && position) {
        result = await apiService.updatePosition(position.id, {
          stop_loss: num(stop) ?? 0, target: num(target) ?? 0, notes: notes || "",
        });
      }
      onDone(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  const input =
    "w-full bg-base-800/80 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-neon-blue/50";

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      <form
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
        className="glass-panel bg-base-900/95 w-full max-w-md p-5 space-y-3"
      >
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold">{title}</h3>
          <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Close">
            ✕
          </button>
        </div>

        {mode === "buy" && !symbol && (
          <label className="block text-xs text-slate-400 space-y-1">
            <span>Stock (name or NSE symbol)</span>
            <input value={pick} onChange={(e) => setPick(e.target.value)} className={input} autoFocus placeholder="e.g. BEL" />
            {suggestions.length > 0 && (
              <div className="glass-panel p-1">
                {suggestions.map((s) => (
                  <button key={s.symbol} type="button" onClick={() => setPick(s.symbol)}
                    className="w-full text-left px-2 py-1 rounded text-xs hover:bg-white/5 flex justify-between">
                    <span>{s.name}</span>
                    <span className="font-mono text-slate-500">{s.symbol}</span>
                  </button>
                ))}
              </div>
            )}
          </label>
        )}

        {mode !== "edit" && (
          <div className="grid grid-cols-3 gap-2">
            <label className="text-xs text-slate-400 space-y-1">
              <span className="block">Quantity</span>
              <input value={quantity} onChange={(e) => setQuantity(e.target.value)} inputMode="decimal" className={input}
                autoFocus={mode === "sell" || !!symbol} />
            </label>
            <label className="text-xs text-slate-400 space-y-1">
              <span className="block">{mode === "buy" ? "Buy price (₹)" : "Sell price (₹)"}</span>
              <input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" className={input} />
            </label>
            <label className="text-xs text-slate-400 space-y-1">
              <span className="block">Date</span>
              <input type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} className={input} />
            </label>
          </div>
        )}

        {mode === "sell" && position && (
          <p className="text-xs text-slate-400">
            You hold {position.quantity} @ ₹{position.avg_price.toLocaleString("en-IN")}.
            {pnlPreview !== null && (
              <span className={pnlPreview >= 0 ? " text-neon-emerald" : " text-neon-rose"}>
                {" "}This sale: {pnlPreview >= 0 ? "+" : "−"}₹{Math.abs(pnlPreview).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </span>
            )}
          </p>
        )}

        {mode !== "sell" && (
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-slate-400 space-y-1">
              <span className="block">Stop-loss (₹)</span>
              <input value={stop} onChange={(e) => setStop(e.target.value)} inputMode="decimal" className={input} placeholder="none" />
            </label>
            <label className="text-xs text-slate-400 space-y-1">
              <span className="block">Target (₹)</span>
              <input value={target} onChange={(e) => setTarget(e.target.value)} inputMode="decimal" className={input} placeholder="none" />
            </label>
          </div>
        )}
        {mode === "buy" && (defaults?.stop_loss || defaults?.target) && (
          <p className="text-[11px] text-slate-500">Stop-loss and target are prefilled from the AI trade plan — change them to your own.</p>
        )}

        {mode !== "sell" && (
          <label className="block text-xs text-slate-400 space-y-1">
            <span>Notes (optional)</span>
            <input value={notes} onChange={(e) => setNotes(e.target.value)} className={input} />
          </label>
        )}

        {error && <p className="text-xs text-neon-rose">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="btn-secondary text-sm">Cancel</button>
          <button type="submit" disabled={saving} className="btn-primary text-sm disabled:opacity-50">
            {saving ? "Saving…" : mode === "buy" ? "Save holding" : mode === "sell" ? "Record sale" : "Save"}
          </button>
        </div>
        <p className="text-[10px] text-slate-600">
          This only records your trade for monitoring and learning — no order is placed.
        </p>
      </form>
    </div>
  );
}
