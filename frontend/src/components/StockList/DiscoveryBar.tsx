import { useEffect, useState } from "react";
import apiService from "../../services/api";
import type { DiscoveryStatus } from "../../services/types";
import InfoTip from "../common/InfoTip";

const POLL_RUNNING_MS = 15000;

/**
 * How today's Auto list was chosen: the whole NIFTY 500 screened on real data, the best
 * candidates reviewed by every enabled AI model, the best of the day kept. Shown above the Auto list.
 */
export default function DiscoveryBar({ refreshKey }: { refreshKey: string }) {
  const [status, setStatus] = useState<DiscoveryStatus | null>(null);
  const [starting, setStarting] = useState(false);

  const load = () => apiService.getDiscoveryStatus().then(setStatus).catch(() => {});

  useEffect(() => { load(); }, [refreshKey]);
  useEffect(() => {
    if (!status?.running) return;
    const timer = window.setInterval(load, POLL_RUNNING_MS);
    return () => window.clearInterval(timer);
  }, [status?.running]);

  const runNow = async () => {
    setStarting(true);
    try {
      await apiService.runDiscovery();
      await load();
      setStatus((s) => (s ? { ...s, running: true } : s));
    } finally {
      setStarting(false);
    }
  };

  if (!status) return null;
  const when = status.run_at ? new Date(status.run_at).toLocaleString("en-IN", {
    weekday: "short", hour: "2-digit", minute: "2-digit" }) : null;
  const filteredOut = Object.entries(status.filtered ?? {}).sort((a, b) => b[1] - a[1]);
  const aiPicks = (status.selected ?? []).filter((p) => p.ai);
  const catalysts = status.catalysts ?? [];
  const caughtToday = (status.selected ?? []).filter((p) => p.catalyst).map((p) => p.symbol);

  return (
    <div className="text-[10px] text-slate-400 leading-snug px-1">
      {status.running ? (
        <span className="text-neon-blue">Picking today's best from the whole market… (real-data screen, then AI review)</span>
      ) : status.available ? (
        <span>
          Today's picks{when ? ` (${when})` : ""}: <span className="text-slate-200">{status.selected_count}</span> best of{" "}
          {status.pool_size} shares · {status.eligible} passed data checks · {status.ai_reviewed} reviewed by AI
          <InfoTip label="How today's Auto list was chosen">
            <span className="block font-semibold mb-1">How this list is chosen (each weekday, 08:20 IST)</span>
            <span className="block">1. Pool: NSE's NIFTY 500 ({status.pool_size} shares).</span>
            <span className="block">2. Real-data screen: every share scored on its actual daily prices and volume,
              its NSE filings and news, delivery %, results dates and F&amp;O ban status. Prices are cross-checked
              against NSE's official close. Filtered out: {filteredOut.length
                ? filteredOut.map(([k, v]) => `${k} ${v}`).join(", ") : "none"}.</span>
            {!!status.filings_read && (
              <span className="block">The AI read {status.filings_read} NSE filing(s) of the best candidates.</span>
            )}
            <span className="block">3. AI review: the best {status.ai_review_shortlist} were analysed by every enabled
              model; a confident combined SELL drops a share for the day, and the AI's probability moves its score
              by up to ±10.{catalysts.length ? ` Also reviewed for today's signals: ${catalysts.map((c) =>
                `${c.symbol} (${c.reasons.join(", ")})`).join("; ")}${caughtToday.length
                ? ` — made the list: ${caughtToday.join(", ")}` : ""}.` : ""}{aiPicks.length ? ` AI view on today's picks: ${aiPicks.slice(0, 5).map((p) =>
                `${p.symbol} ${p.ai!.signal} ${Math.round(p.ai!.probability_up)}%`).join(", ")}.` : ""}</span>
            <span className="block">4. The best {status.auto_list_size} make the list; a share already on it stays while
              it ranks in the top {Math.round(status.auto_list_size * 1.4)}.</span>
            {status.top_sectors && status.top_sectors.length > 0 && (
              <span className="block mt-1">Leading sectors today: {status.top_sectors.join(", ")}.</span>
            )}
            {status.added && status.added.length > 0 && (
              <span className="block mt-1">New today: {status.added.join(", ")}.</span>
            )}
            {status.sources && (
              <span className="block mt-1">Data sources: {[
                ["filings", "NSE filings"], ["calendar", "results calendar"], ["bhavcopy", "NSE closing prices & delivery"],
                ["ban", "F&O ban list"], ["deals", "bulk/block deals"],
              ].map(([k, label]) => {
                const s = status.sources![k];
                return `${label} ${!s ? "not fetched yet" : s.ok ? "✓" : "✗ unavailable"}`;
              }).join(" · ")} · prices from the market feed · news from 4 financial news sites.</span>
            )}
            <span className="block mt-1">Remove a share from Auto and it won't be picked again; your Manual list and
              holdings are never changed.</span>
          </InfoTip>
          {status.note && <span className="block text-amber-300">{status.note}</span>}
        </span>
      ) : (
        <span>Not picked yet — the first market-wide pick runs shortly after start-up.</span>
      )}
      {!status.running && (
        <div className="flex items-center gap-1 mt-1">
          <button onClick={runNow} disabled={starting}
            className="text-[11px] px-2 py-0.5 rounded-md border border-neon-blue/40 text-neon-blue hover:bg-neon-blue/10 disabled:opacity-50">
            {starting ? "Starting…" : "Find today's best now"}
          </button>
          <InfoTip label="About finding today's best now">
            Screens the whole NIFTY 500 again on current prices, has every enabled AI model analyse the best
            {` ${status.ai_review_shortlist}`}, and relists the Auto tab. AI analyses made in the last hour are reused;
            others are redone, so each press can cost up to {status.ai_review_shortlist} AI analyses per model
            (paid models only). Takes a few minutes. It also runs automatically each weekday at 08:20.
          </InfoTip>
        </div>
      )}
    </div>
  );
}
