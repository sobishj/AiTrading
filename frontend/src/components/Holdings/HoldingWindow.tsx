import { useCallback, useEffect, useState } from "react";
import apiService from "../../services/api";
import type { Position, PositionAlert, PositionTransaction } from "../../services/types";

interface HoldingWindowProps {
  positionId: number;
  onClose: () => void;
  /** Called after any change so lists and the trade plan refresh. */
  onChanged: () => void;
}

const inr = (v: number, d = 2) => `₹${v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const today = () => new Date().toISOString().slice(0, 10);
const field =
  "w-full bg-base-800/80 border border-white/10 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-neon-blue/50";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-[11px] uppercase tracking-widest text-slate-500">{title}</h4>
      {children}
    </section>
  );
}

/** One editable row of the holding's history (a buy or a sale). */
function TransactionRow({ tx, onSave, onDelete }: {
  tx: PositionTransaction;
  onSave: (q: number, p: number, d: string) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [q, setQ] = useState(String(tx.quantity));
  const [p, setP] = useState(String(tx.price));
  const [d, setD] = useState(tx.date);

  if (editing) {
    return (
      <tr className="border-t border-white/5">
        <td className={`py-1 font-semibold ${tx.side === "BUY" ? "text-neon-blue" : "text-neon-purple"}`}>{tx.side}</td>
        <td><input type="date" value={d} onChange={(e) => setD(e.target.value)} className={field} /></td>
        <td><input value={q} onChange={(e) => setQ(e.target.value)} inputMode="decimal" className={field} /></td>
        <td><input value={p} onChange={(e) => setP(e.target.value)} inputMode="decimal" className={field} /></td>
        <td colSpan={2} className="text-right whitespace-nowrap">
          <button onClick={async () => { await onSave(Number(q), Number(p), d); setEditing(false); }} className="text-neon-emerald hover:underline mr-2">Save</button>
          <button onClick={() => setEditing(false)} className="text-slate-400 hover:underline">Cancel</button>
        </td>
      </tr>
    );
  }
  return (
    <tr className="border-t border-white/5">
      <td className={`py-1.5 font-semibold ${tx.side === "BUY" ? "text-neon-blue" : "text-neon-purple"}`}>{tx.side}</td>
      <td>{tx.date}</td>
      <td className="font-mono">{tx.quantity}</td>
      <td className="font-mono">{inr(tx.price)}</td>
      <td className={`font-mono text-right ${(tx.realized_pnl ?? 0) >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
        {tx.realized_pnl !== null ? inr(tx.realized_pnl, 0) : ""}
      </td>
      <td className="text-right whitespace-nowrap">
        {confirming ? (
          <>
            <button onClick={async () => { await onDelete(); setConfirming(false); }} className="text-neon-rose hover:underline mr-2">Delete</button>
            <button onClick={() => setConfirming(false)} className="text-slate-400 hover:underline">Keep</button>
          </>
        ) : (
          <>
            <button onClick={() => setEditing(true)} className="text-neon-blue hover:underline mr-2">Edit</button>
            <button onClick={() => setConfirming(true)} className="text-slate-500 hover:text-neon-rose">✕</button>
          </>
        )}
      </td>
    </tr>
  );
}

/**
 * Everything about one holding: what you entered (editable), the AI's current
 * suggestion (sell / hold, suggested stop-loss and target), marking it sold,
 * and its alerts.
 */
export default function HoldingWindow({ positionId, onClose, onChanged }: HoldingWindowProps) {
  const [position, setPosition] = useState<Position | null>(null);
  const [alerts, setAlerts] = useState<PositionAlert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  const [notes, setNotes] = useState("");
  const [sellQty, setSellQty] = useState("");
  const [sellPrice, setSellPrice] = useState("");
  const [sellDate, setSellDate] = useState(today());
  const [confirmDelete, setConfirmDelete] = useState(false);

  const applyPosition = useCallback((p: Position, resetForms: boolean) => {
    setPosition(p);
    if (resetForms) {
      // Empty levels are auto-filled with the AI's suggestion.
      setStop(String(p.stop_loss ?? p.suggestion?.suggested_stop ?? ""));
      setTarget(String(p.target ?? p.suggestion?.suggested_target ?? ""));
      setNotes(p.notes ?? "");
      setSellQty(String(p.quantity || ""));
      setSellPrice(p.last_price ? String(p.last_price) : "");
    }
  }, []);

  const load = useCallback(async (resetForms = false) => {
    try {
      const p = await apiService.getPosition(positionId);
      applyPosition(p, resetForms);
      const a = await apiService.getAlerts(100);
      setAlerts(a.alerts.filter((x) => x.position_id === positionId).slice(0, 6));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the holding");
    }
  }, [positionId, applyPosition]);

  useEffect(() => {
    load(true);
  }, [load]);

  const run = async (key: string, action: () => Promise<Position | null | void>, note: string, resetForms = true) => {
    setSaving(key);
    setError(null);
    setSavedNote(null);
    try {
      const result = await action();
      if (result === null) {
        onChanged();
        onClose();
        return;
      }
      await load(resetForms);
      setSavedNote(note);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally {
      setSaving(null);
    }
  };

  if (!position) {
    return (
      <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center" onClick={onClose}>
        <div className="glass-panel bg-base-900/95 p-6 text-sm text-slate-400">{error ?? "Loading holding…"}</div>
      </div>
    );
  }

  const s = position.suggestion;
  const open = position.status === "open";
  const up = position.unrealized_pnl >= 0;
  const risk = position.loss_risk ?? 0;
  const stopHit = s?.action === "SELL" && s.reason.toLowerCase().includes("stop");
  const bannerClass = !s ? "" : s.action === "SELL"
    ? stopHit ? "border-neon-rose/50 bg-neon-rose/10" : "border-neon-emerald/50 bg-neon-emerald/10"
    : s.action === "CONSIDER_SELLING" ? "border-amber-300/50 bg-amber-300/10" : "border-white/10 bg-white/5";
  const actionLabel = !s ? "" : s.action === "SELL" ? "AI suggests: SELL" : s.action === "CONSIDER_SELLING" ? "AI suggests: consider selling" : "AI suggests: HOLD";
  const sellPnl = Number(sellQty) > 0 && Number(sellPrice) > 0 ? (Number(sellPrice) - position.avg_price) * Number(sellQty) : null;

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="glass-panel bg-base-900/95 w-full max-w-2xl max-h-[92vh] overflow-y-auto p-5 space-y-5" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold">{position.name} <span className="text-xs font-mono text-slate-500">NSE:{position.symbol}</span></h3>
            <p className="text-xs text-slate-400">
              {open ? `${position.quantity} shares @ ${inr(position.avg_price)} · invested ${inr(position.invested, 0)}` : "Closed holding"}
              {position.last_price !== null && ` · now ${inr(position.last_price)}`}
            </p>
          </div>
          <div className="text-right">
            {open && (
              <div className={`text-lg font-mono font-semibold ${up ? "text-neon-emerald" : "text-neon-rose"}`}>
                {up ? "+" : "−"}{inr(Math.abs(position.unrealized_pnl), 0)}
                {position.unrealized_pct !== null && <span className="text-sm"> ({up ? "+" : ""}{position.unrealized_pct.toFixed(1)}%)</span>}
              </div>
            )}
            {position.realized_pnl !== 0 && (
              <div className={`text-xs font-mono ${position.realized_pnl >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>booked {inr(position.realized_pnl, 0)}</div>
            )}
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Close">✕</button>
        </div>

        {/* AI suggestion */}
        {open && s && (
          <div className={`rounded-xl border px-4 py-3 space-y-2 ${bannerClass}`}>
            <div className="flex items-center justify-between gap-3">
              <span className={`text-sm font-bold ${s.action === "SELL" ? (stopHit ? "text-neon-rose" : "text-neon-emerald") : s.action === "CONSIDER_SELLING" ? "text-amber-300" : "text-slate-200"}`}>
                {actionLabel}
              </span>
              {s.action !== "HOLD" && position.last_price && (
                <button
                  disabled={saving !== null}
                  onClick={() => run("sell", () => apiService.sellShares(position.id, { quantity: position.quantity, price: position.last_price!, trade_date: today() }),
                    `Marked all ${position.quantity} shares sold at ${inr(position.last_price!)}.`)}
                  className="btn-primary text-xs py-1.5 disabled:opacity-50"
                >
                  Mark all sold at {inr(position.last_price)}
                </button>
              )}
            </div>
            <p className="text-xs text-slate-300">{s.reason}</p>
            <div className="h-1.5 rounded-full bg-white/5 overflow-hidden" title="Risk of loss">
              <div className={`h-full rounded-full ${risk >= 60 ? "bg-neon-rose" : risk >= 35 ? "bg-amber-300" : "bg-neon-emerald"}`} style={{ width: `${Math.max(3, risk)}%` }} />
            </div>
            <p className="text-[11px] text-slate-500">
              Risk of loss {risk.toFixed(0)}/100{position.risk_reasons.length > 0 && ` — ${position.risk_reasons.slice(0, 3).join("; ")}`}
            </p>
          </div>
        )}

        {/* What you entered */}
        <Section title="Your entries (click Edit to correct)">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 text-left">
                <th className="font-normal py-1">Type</th><th className="font-normal">Date</th><th className="font-normal">Qty</th>
                <th className="font-normal">Price</th><th className="font-normal text-right">P&L</th><th />
              </tr>
            </thead>
            <tbody>
              {position.transactions.map((tx) => (
                <TransactionRow
                  key={tx.id}
                  tx={tx}
                  onSave={(q, p, d) => run(`tx${tx.id}`, () => apiService.editTransaction(position.id, tx.id, { quantity: q, price: p, trade_date: d }), "Entry updated.", false)}
                  onDelete={() => run(`del${tx.id}`, () => apiService.deleteTransaction(position.id, tx.id), "Entry deleted.")}
                />
              ))}
            </tbody>
          </table>
        </Section>

        {/* Stop-loss & target with the AI suggestion below */}
        {open && (
          <Section title="Stop-loss & target (sell value)">
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Stop-loss (₹)</span>
                <input value={stop} onChange={(e) => setStop(e.target.value)} inputMode="decimal" className={field} /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Target / sell value (₹)</span>
                <input value={target} onChange={(e) => setTarget(e.target.value)} inputMode="decimal" className={field} /></label>
            </div>
            <label className="block text-xs text-slate-400 space-y-1"><span>Notes</span>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} className={field} /></label>
            {s && (
              <div className="flex items-center justify-between gap-2 rounded-lg border border-neon-purple/30 bg-neon-purple/5 px-3 py-2">
                <span className="text-xs text-slate-300">
                  AI suggestion now: stop-loss <b className="text-neon-rose">{inr(s.suggested_stop)}</b> · target <b className="text-neon-emerald">{inr(s.suggested_target)}</b>
                  {s.r_multiple !== null && <span className="text-slate-500"> · {s.r_multiple >= 0 ? "+" : ""}{s.r_multiple.toFixed(1)}R</span>}
                </span>
                <button onClick={() => { setStop(String(s.suggested_stop)); setTarget(String(s.suggested_target)); }}
                  className="text-[11px] px-2 py-1 rounded-md border border-neon-purple/40 text-neon-purple hover:bg-neon-purple/10 shrink-0">
                  Use AI values
                </button>
              </div>
            )}
            <div className="flex justify-end">
              <button
                disabled={saving !== null}
                onClick={() => run("levels", () => apiService.updatePosition(position.id, {
                  stop_loss: Number(stop) || 0, target: Number(target) || 0, notes,
                }), "Stop-loss and target saved.")}
                className="btn-secondary text-sm disabled:opacity-50"
              >
                {saving === "levels" ? "Saving…" : "Save stop-loss & target"}
              </button>
            </div>
          </Section>
        )}

        {/* Mark as sold */}
        {open && (
          <Section title="Mark as sold">
            <div className="grid grid-cols-3 gap-2">
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Quantity (of {position.quantity})</span>
                <input value={sellQty} onChange={(e) => setSellQty(e.target.value)} inputMode="decimal" className={field} /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Sell price (₹)</span>
                <input value={sellPrice} onChange={(e) => setSellPrice(e.target.value)} inputMode="decimal" className={field} /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Date</span>
                <input type="date" value={sellDate} onChange={(e) => setSellDate(e.target.value)} className={field} /></label>
            </div>
            <div className="flex items-center justify-between">
              <span className={`text-xs ${sellPnl === null ? "text-slate-500" : sellPnl >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
                {sellPnl !== null ? `This sale: ${sellPnl >= 0 ? "+" : "−"}${inr(Math.abs(sellPnl), 0)}` : "Enter the quantity and price you sold at"}
              </span>
              <button
                disabled={saving !== null || !(Number(sellQty) > 0 && Number(sellPrice) > 0)}
                onClick={() => run("sell", () => apiService.sellShares(position.id, { quantity: Number(sellQty), price: Number(sellPrice), trade_date: sellDate }),
                  `Recorded the sale of ${sellQty} shares at ${inr(Number(sellPrice))}.`)}
                className="btn-primary text-sm disabled:opacity-50"
              >
                {saving === "sell" ? "Saving…" : Number(sellQty) >= position.quantity ? "Mark as sold" : "Record partial sale"}
              </button>
            </div>
          </Section>
        )}

        {error && <p className="text-xs text-neon-rose">{error}</p>}
        {savedNote && <p className="text-xs text-neon-emerald">{savedNote}</p>}

        {/* Alerts */}
        {alerts.length > 0 && (
          <Section title="Alerts for this holding">
            <ul className="space-y-1">
              {alerts.map((a) => (
                <li key={a.id} className="text-xs">
                  <span className={a.severity === "critical" ? "text-neon-rose" : a.severity === "warning" ? "text-amber-300" : "text-neon-blue"}>{a.title}</span>
                  <span className="text-slate-500"> · {new Date(a.created_at + "Z").toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        <div className="flex items-center justify-between pt-2 border-t border-white/5">
          <p className="text-[10px] text-slate-600">Records only — AiTrading never places orders. Sales feed the learning engine.</p>
          {confirmDelete ? (
            <span className="text-xs">
              Delete this whole holding?{" "}
              <button onClick={() => run("delete", async () => { await apiService.deletePosition(position.id); return null; }, "")} className="text-neon-rose hover:underline mx-1">Delete</button>
              <button onClick={() => setConfirmDelete(false)} className="text-slate-400 hover:underline">Keep</button>
            </span>
          ) : (
            <button onClick={() => setConfirmDelete(true)} className="text-xs text-slate-500 hover:text-neon-rose">Delete holding</button>
          )}
        </div>
      </div>
    </div>
  );
}
