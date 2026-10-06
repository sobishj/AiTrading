import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import apiService from "../../services/api";
import type { OptionChain } from "../../services/types";
import InfoTip from "../common/InfoTip";

const n = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
const compact = (v: number | null | undefined) =>
  v == null ? "—" : Math.abs(v) >= 1e5 ? `${(v / 1e5).toFixed(1)}L` : v.toLocaleString("en-IN");
const signed = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${compact(v)}`;

/** NSE's option chain for one expiry, with the summary traders read from it. Data is NSE's, as of its timestamp. */
export default function OptionChainModal({ symbol, name, onClose }: { symbol: string; name?: string; onClose: () => void }) {
  const [expiry, setExpiry] = useState<string | undefined>(undefined);
  const [data, setData] = useState<OptionChain | null>(null);
  const [loading, setLoading] = useState(true);

  const load = (exp?: string) => {
    setLoading(true);
    apiService.getOptionChain(symbol, exp)
      .then(setData)
      .catch((e) => setData({ available: false, symbol, error: e instanceof Error ? e.message : "Couldn't load the option chain" }))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(expiry); }, [symbol, expiry]);

  const s = data?.summary;
  const maxOi = Math.max(1, ...(data?.rows ?? []).flatMap((r) => [r.ce_oi ?? 0, r.pe_oi ?? 0]));
  const card = (label: string, value: string, tip: string) => (
    <div className="glass-panel px-3 py-2 min-w-[7rem]">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}<InfoTip>{tip}</InfoTip></div>
      <div className="text-sm font-mono text-slate-100">{value}</div>
    </div>
  );

  // Portalled to <body>: inside the chart panel, its backdrop blur would clip and shrink a fixed overlay.
  return createPortal(
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="glass-panel bg-base-900/95 max-w-5xl w-full max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 px-5 pt-4 pb-3 border-b border-white/5 flex-wrap">
          <h3 className="text-base font-semibold mr-auto">
            Option chain — {name ?? symbol}
            {data?.available && <span className="ml-2 text-xs font-normal text-slate-400">
              underlying ₹{n(data.underlying, 2)}{data.lot_size ? ` · lot ${data.lot_size}` : ""}</span>}
          </h3>
          {data?.expiries && data.expiries.length > 0 && (
            <select value={data.expiry ?? ""} onChange={(e) => setExpiry(e.target.value)}
              className="bg-base-800/80 border border-white/10 rounded-lg px-2 py-1 text-xs" aria-label="Expiry">
              {data.expiries.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          )}
          <button onClick={() => load(expiry)} disabled={loading} className="btn-secondary text-xs disabled:opacity-50">
            {loading ? "Loading…" : "Refresh"}
          </button>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Close">✕</button>
        </div>

        {data && !data.available && <p className="px-5 py-6 text-sm text-neon-rose">{data.error}</p>}
        {data?.available && s && (
          <>
            <div className="flex gap-2 px-5 py-3 flex-wrap">
              {card("PCR (OI)", n(s.pcr, 2), "Total put open interest ÷ total call open interest for this expiry. Above 1: more puts written than calls (often read as support beneath the price); below 0.7: call writers dominate.")}
              {card("Max pain", n(s.max_pain), "The expiry price at which option buyers, in total, would get the least. Prices are sometimes drawn toward it near expiry — a tendency, not a rule.")}
              {card("ATM IV", s.atm_iv != null ? `${n(s.atm_iv, 1)}%` : "—", "Implied volatility at the strike nearest the price: how big a move option prices assume. Higher = options are dearer.")}
              {card("Support (PE OI)", n(s.support), "Strike at or below the price with the most put open interest — where put writers are defending.")}
              {card("Resistance (CE OI)", n(s.resistance), "Strike at or above the price with the most call open interest — where call writers are defending.")}
            </div>
            <div className="px-5 text-[11px] text-slate-500">
              NSE data as of {data.as_of} (refreshed at most once a minute). Outside market hours it shows the last session.
              Showing {data.rows?.length} of {data.strikes_total} strikes around the price; the summary uses all of them.
            </div>
            <div className="flex-1 overflow-auto px-5 py-3">
              <table className="w-full text-[11px] font-mono">
                <thead className="sticky top-0 bg-base-900">
                  <tr className="text-slate-400">
                    <th colSpan={5} className="text-center py-1 text-neon-emerald font-sans">CALLS</th>
                    <th />
                    <th colSpan={5} className="text-center py-1 text-neon-rose font-sans">PUTS</th>
                  </tr>
                  <tr className="text-slate-500 text-right">
                    <th className="px-1">OI</th><th className="px-1">Chg OI</th><th className="px-1">IV</th>
                    <th className="px-1">Chg</th><th className="px-1">LTP</th>
                    <th className="px-2 text-center">Strike</th>
                    <th className="px-1">LTP</th><th className="px-1">Chg</th><th className="px-1">IV</th>
                    <th className="px-1">Chg OI</th><th className="px-1">OI</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows!.map((r) => {
                    const atm = r.strike === s.atm_strike;
                    const ceItm = r.strike < (data.underlying ?? 0);
                    const peItm = r.strike > (data.underlying ?? 0);
                    const bar = (oi: number | null, color: string) => (
                      <span className={`absolute inset-y-0 ${color} opacity-20`} style={{ width: `${((oi ?? 0) / maxOi) * 100}%` }} />
                    );
                    return (
                      <tr key={r.strike} className={`text-right border-t border-white/5 ${atm ? "outline outline-1 outline-neon-blue/50" : ""}`}>
                        <td className={`px-1 relative ${ceItm ? "bg-amber-300/5" : ""}`}>{bar(r.ce_oi, "right-0 bg-neon-emerald")}<span className="relative">{compact(r.ce_oi)}</span></td>
                        <td className={`px-1 ${ceItm ? "bg-amber-300/5" : ""} ${(r.ce_oi_change ?? 0) < 0 ? "text-neon-rose" : "text-slate-300"}`}>{signed(r.ce_oi_change)}</td>
                        <td className={`px-1 ${ceItm ? "bg-amber-300/5" : ""}`}>{n(r.ce_iv, 1)}</td>
                        <td className={`px-1 ${ceItm ? "bg-amber-300/5" : ""} ${(r.ce_change ?? 0) < 0 ? "text-neon-rose" : "text-neon-emerald"}`}>{n(r.ce_change, 2)}</td>
                        <td className={`px-1 text-slate-100 ${ceItm ? "bg-amber-300/5" : ""}`}>{n(r.ce_ltp, 2)}</td>
                        <td className={`px-2 text-center font-semibold ${atm ? "text-neon-blue" : "text-slate-200"}`}>{n(r.strike)}</td>
                        <td className={`px-1 text-slate-100 ${peItm ? "bg-amber-300/5" : ""}`}>{n(r.pe_ltp, 2)}</td>
                        <td className={`px-1 ${peItm ? "bg-amber-300/5" : ""} ${(r.pe_change ?? 0) < 0 ? "text-neon-rose" : "text-neon-emerald"}`}>{n(r.pe_change, 2)}</td>
                        <td className={`px-1 ${peItm ? "bg-amber-300/5" : ""}`}>{n(r.pe_iv, 1)}</td>
                        <td className={`px-1 ${peItm ? "bg-amber-300/5" : ""} ${(r.pe_oi_change ?? 0) < 0 ? "text-neon-rose" : "text-slate-300"}`}>{signed(r.pe_oi_change)}</td>
                        <td className={`px-1 relative ${peItm ? "bg-amber-300/5" : ""}`}>{bar(r.pe_oi, "left-0 bg-neon-rose")}<span className="relative">{compact(r.pe_oi)}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="px-5 pb-3 text-[10px] text-slate-500">
              Shaded cells are in the money. Bars show open interest. This is information, not advice: options can lose
              their whole premium, and AiTrading doesn't place orders.
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
