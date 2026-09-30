import { useResource } from "../../hooks/useResource";
import apiService from "../../services/api";

const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;

/** Your trading style, measured from the trades you recorded — the same profile every AI model is given. */
export default function TradingStyleSection({ version }: { version: number }) {
  const { data } = useResource(() => apiService.getTradingStyle(), [version], true);
  const s = data?.stats;

  return (
    <section className="space-y-2">
      <h4 className="text-xs uppercase tracking-widest text-slate-400">Your trading style (from your recorded trades)</h4>
      {!s || s.entries === 0 ? (
        <p className="text-xs text-slate-500">
          No trades analysed yet. Record buys and sales in Holdings, upload a Zerodha report, or just tell the chat
          (e.g. "I bought 10 ITC at 265 and sold at 280 yesterday") and confirm.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-4 gap-2 text-[10px] text-slate-500">
            <div>Buys<span className="block text-xs text-slate-300 font-mono">{s.entries}</span></div>
            <div>Closed trades<span className="block text-xs text-slate-300 font-mono">{s.closed_trades ?? 0}</span></div>
            <div>Win rate<span className="block text-xs text-slate-300 font-mono">{s.win_rate != null ? `${s.win_rate.toFixed(0)}%` : "—"}</span></div>
            <div>Avg win / loss<span className="block text-xs text-slate-300 font-mono">{pct(s.avg_win_pct)} / {pct(s.avg_loss_pct)}</span></div>
          </div>
          <ul className="space-y-0.5">
            {data!.style_notes.map((n) => (
              <li key={n} className="text-xs text-slate-300 flex gap-2">
                <span className="mt-1.5 w-1 h-1 rounded-full shrink-0 bg-neon-purple" />
                <span>{n}</span>
              </li>
            ))}
          </ul>
          {data!.entries.length > 0 && (
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-slate-500 text-left">
                  <th className="font-normal">Buy</th>
                  <th className="font-normal">Chart when you bought</th>
                  <th className="font-normal">AI view</th>
                  <th className="font-normal text-right">+10 days</th>
                </tr>
              </thead>
              <tbody>
                {data!.entries.slice(-8).reverse().map((e) => (
                  <tr key={`${e.symbol}-${e.day}-${e.price}`} className="text-slate-300 align-top">
                    <td className="whitespace-nowrap">{e.symbol} {e.day}<span className="block text-slate-500">{e.quantity} @ ₹{e.price}</span></td>
                    <td className="text-slate-400">
                      {[...e.style, ...e.factors].slice(0, 5).map((f) => f.replace(/_/g, " ")).join(", ") || "—"}
                      {e.ret_5d_before != null && <span className="text-slate-600"> · 5-day move before {pct(e.ret_5d_before)}</span>}
                    </td>
                    <td className={e.ai_alignment === "followed" ? "text-neon-emerald" : e.ai_alignment === "against" ? "text-neon-rose" : "text-slate-500"}>
                      {e.ai_view ?? "none"}
                    </td>
                    <td className="text-right font-mono">{e.fwd?.["10"] != null ? pct(e.fwd["10"]) : "pending"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-[10px] text-slate-600">
            Every AI model gets this profile to tailor entries, stops, targets and warnings to how you trade. It never
            changes their forecast of where a price goes. Patterns appear from 3 trades and are marked "early sign" until 10.
            {data!.updated_at && ` Updated ${new Date(data!.updated_at).toLocaleString("en-IN")}.`}
          </p>
        </>
      )}
    </section>
  );
}
