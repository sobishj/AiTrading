import { ChangeEvent, useEffect, useState } from "react";
import { useResource } from "../../hooks/useResource";
import { useSettings } from "../../hooks/useSettings";
import apiService from "../../services/api";
import type { TradeUploadResult } from "../../services/types";
import ModelEvidenceSection from "./ModelEvidenceSection";
import TradingStyleSection from "./TradingStyleSection";

interface LearningModalProps {
  onClose: () => void;
}

const OUTCOME_STYLES: Record<string, string> = {
  profit: "text-neon-emerald",
  target_hit: "text-neon-emerald",
  expired_profit: "text-neon-emerald",
  loss: "text-neon-rose",
  stop_hit: "text-neon-rose",
  expired_loss: "text-neon-rose",
};

/** PRD §14-16: upload Zerodha trades, see what the strategy library has learned, and set position sizing. */
export default function LearningModal({ onClose }: LearningModalProps) {
  const [version, setVersion] = useState(0);
  const strategies = useResource(() => apiService.getStrategies(), [version], true);
  const trades = useResource(() => apiService.getTradeHistory(30), [version], true);
  const ai = useResource(() => apiService.getAIPerformance(), [version], true);
  const holdingsLearning = useResource(() => apiService.getHoldingsLearning(), [version], true);
  const aiForecasts = useResource(() => apiService.getAIPredictions(15), [version], true);
  const [aiMessage, setAiMessage] = useState<string | null>(null);
  const { settings, saving, error: settingsError, setRisk } = useSettings();

  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<TradeUploadResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [capital, setCapital] = useState("");
  const [riskPct, setRiskPct] = useState("");

  useEffect(() => {
    if (settings) {
      setCapital(String(settings.capital ?? ""));
      setRiskPct(String(settings.risk_per_trade_pct ?? ""));
    }
  }, [settings]);

  const upload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    setResult(null);
    try {
      setResult(await apiService.uploadTrades(file));
      setVersion((v) => v + 1);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const queueForecasts = async () => {
    setAiMessage(null);
    try {
      await apiService.queueAIForecasts();
      setAiMessage("Queued: Qwen is forecasting today's top stocks and your Manual list (about 20–40 s each).");
    } catch (err) {
      setAiMessage(err instanceof Error ? err.message : "Could not queue forecasts");
    }
  };

  const togglePractice = async () => {
    if (!ai.data) return;
    await apiService.setAIPractice(!ai.data.practice_enabled);
    setVersion((v) => v + 1);
  };

  const queueOutlook = async () => {
    setAiMessage(null);
    try {
      await apiService.queueAIOutlook();
      setAiMessage("Queued: Qwen is reading today's headlines and global cues for the next-session outlook.");
    } catch (err) {
      setAiMessage(err instanceof Error ? err.message : "Could not queue the outlook");
    }
  };

  const queueReflection = async () => {
    setAiMessage(null);
    try {
      const result = await apiService.queueAIReflection();
      setAiMessage(`Graded ${result.graded} due forecast(s); Qwen is reviewing its results and rewriting its lessons.`);
      setVersion((v) => v + 1);
    } catch (err) {
      setAiMessage(err instanceof Error ? err.message : "Could not start the review");
    }
  };

  const saveRisk = () => {
    const c = Number(capital);
    const r = Number(riskPct);
    if (c > 0 && r > 0 && r <= 10) setRisk(c, r);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      <div
        className="glass-panel bg-base-900/95 max-w-4xl w-full max-h-[88vh] overflow-y-auto p-6 space-y-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Learning Engine</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Close">
            ✕
          </button>
        </div>

        <section className="space-y-2">
          <h4 className="text-xs uppercase tracking-widest text-slate-400">Upload Zerodha trade report</h4>
          <p className="text-xs text-slate-500">
            Console → Reports → Tradebook → download CSV or XLSX. Trades are matched into round trips, linked to the
            recommendations that preceded them, and used to re-score each strategy. Re-uploading the same file is safe.
          </p>
          <label className={`btn-primary inline-block cursor-pointer ${uploading ? "opacity-50 pointer-events-none" : ""}`}>
            {uploading ? "Importing…" : "Choose tradebook file"}
            <input type="file" accept=".csv,.xlsx,.xls" className="hidden" onChange={upload} />
          </label>
          {uploadError && <p className="text-xs text-neon-rose">{uploadError}</p>}
          {result && (
            <div className="text-xs text-slate-300 space-y-1">
              <p>
                {result.rows_processed} executions read · {result.rows_imported} closed trades imported ·{" "}
                {result.open_positions} open positions · {result.linked_to_recommendations} linked to AiTrading calls ·{" "}
                {result.duplicates_skipped} duplicates skipped · {result.graded} recommendations auto-graded
              </p>
              {result.recalibration && (
                <p className="text-slate-500">
                  Weight recalibration: {result.recalibration.status.replace("_", " ")} (sample {result.recalibration.sample_size})
                </p>
              )}
              {result.errors.length > 0 && (
                <details className="text-neon-rose">
                  <summary>{result.errors.length} row(s) skipped</summary>
                  <ul className="list-disc pl-5">
                    {result.errors.slice(0, 20).map((err) => (
                      <li key={err}>{err}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </section>

        {holdingsLearning.data && (
          <section className="space-y-2">
            <h4 className="text-xs uppercase tracking-widest text-slate-400">Your holdings — what the AI learned</h4>
            <p className="text-xs text-slate-500">
              Every sale you record becomes a real trade (quantity and rupee P&L) in the strategy statistics. Each
              "risk of loss" / "near stop" warning is checked 5 trading days later — did the price actually fall? —
              and the warning threshold adapts: fewer false alarms if warnings were often wrong, earlier warnings if
              they were usually right.
            </p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <div className="glass-panel px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Booked P&L</div>
                <div className={`text-sm font-semibold font-mono ${holdingsLearning.data.realized_pnl >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
                  ₹{holdingsLearning.data.realized_pnl.toLocaleString("en-IN")}
                </div>
              </div>
              <div className="glass-panel px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Closed holdings</div>
                <div className="text-sm font-semibold font-mono">
                  {holdingsLearning.data.closed_positions}
                  {holdingsLearning.data.closed_win_rate !== null && ` · ${holdingsLearning.data.closed_win_rate.toFixed(0)}% won`}
                </div>
              </div>
              {holdingsLearning.data.alerts.map((a) => (
                <div key={a.kind} className="glass-panel px-3 py-2">
                  <div className="text-[11px] uppercase tracking-wide text-slate-500">{a.kind === "loss_risk" ? "Risk warnings" : "Near-stop warnings"}</div>
                  <div className="text-sm font-semibold font-mono">{a.precision !== null ? `${a.precision.toFixed(0)}% right` : "—"}</div>
                  <div className="text-[10px] text-slate-500">{a.graded} graded</div>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-slate-500">
              Current loss-risk alert threshold: {holdingsLearning.data.risk_threshold}/100
              {holdingsLearning.data.risk_threshold !== holdingsLearning.data.base_risk_threshold
                ? ` (adapted from ${holdingsLearning.data.base_risk_threshold})` : " (adapts after 20 graded warnings)"}
            </p>
          </section>
        )}

        <section className="space-y-2">
          <h4 className="text-xs uppercase tracking-widest text-slate-400">AI analyst (Qwen) track record</h4>
          <p className="text-xs text-slate-500">
            Qwen reads every new headline, forecasts the top stocks and your Manual list each weekday morning, and
            is graded {ai.data?.horizon_days ?? 5} trading days later. After grading it reviews its hits and misses and rewrites
            its lessons, which feed every later forecast. It only influences the ranking once{" "}
            {ai.data?.min_graded_for_trust ?? 30} forecasts are graded and it beats the naive baseline. Its model weights
            never change; its track record, lessons and news reads do.
          </p>
          {ai.data && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              {[
                ["Forecasts", `${ai.data.total_forecasts}`, `${ai.data.pending} awaiting grading`],
                ["Hit rate", ai.data.hit_rate !== null ? `${ai.data.hit_rate.toFixed(0)}%` : "—", ai.data.baseline_hit_rate !== null ? `baseline ${ai.data.baseline_hit_rate.toFixed(0)}%` : "needs graded forecasts"],
                ["Calibration (Brier)", ai.data.brier !== null ? ai.data.brier.toFixed(3) : "—", "lower is better · 0.25 = coin flip"],
                ["Earned weight", ai.data.trust_weight > 0 ? `±${ai.data.trust_weight.toFixed(1)} pts` : "0 (shadow)", `${ai.data.graded}/${ai.data.min_graded_for_trust} graded`],
                ["Lessons", `v${ai.data.lessons_version}`, ai.data.lessons.length ? `${ai.data.lessons.length} active` : "written after first grades"],
              ].map(([label, value, hint]) => (
                <div key={label} className="glass-panel px-3 py-2">
                  <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
                  <div className="text-sm font-semibold font-mono text-slate-200">{value}</div>
                  <div className="text-[10px] text-slate-500">{hint}</div>
                </div>
              ))}
            </div>
          )}
          {ai.data && (
            <div className="glass-panel px-3 py-2 space-y-1">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="text-xs text-slate-300 font-medium">
                  Chart practice (market closed){" "}
                  <span className={ai.data.practicing_now && ai.data.practice_enabled ? "text-neon-emerald" : "text-slate-500"}>
                    · {ai.data.practice_enabled ? (ai.data.practicing_now ? "practising now" : "paused while the market is open") : "off"}
                  </span>
                </span>
                <button onClick={togglePractice} className="text-[11px] px-2 py-0.5 rounded-md border border-white/10 text-slate-300 hover:border-neon-blue/40">
                  {ai.data.practice_enabled ? "Turn off" : "Turn on"}
                </button>
              </div>
              <p className="text-xs text-slate-400">
                {ai.data.practice.graded} historical cases practised ({ai.data.practice_today} today)
                {ai.data.practice.hit_rate !== null &&
                  ` · ${ai.data.practice.hit_rate.toFixed(0)}% correct vs ${ai.data.practice.baseline_hit_rate?.toFixed(0)}% baseline · Brier ${ai.data.practice.brier?.toFixed(3)}`}
              </p>
              {ai.data.practice.by_lessons_version.length > 0 && (
                <p className="text-[11px] text-slate-500">
                  Hit rate by lessons version:{" "}
                  {ai.data.practice.by_lessons_version.map((v) => (
                    <span key={v.version} className="mr-3 font-mono">
                      v{v.version}: {v.hit_rate.toFixed(0)}% ({v.forecasts})
                    </span>
                  ))}
                </p>
              )}
              <p className="text-[11px] text-slate-500">
                Practice replays a random stock at a random past date, showing Qwen only the chart up to that day (date
                hidden), and grades it immediately. It trains the lessons but never earns ranking weight — only live
                forecasts do. It pauses while you chat.
              </p>
              <p className="text-xs text-slate-400 pt-1">
                Next-session outlooks: {ai.data.outlook.total} made
                {ai.data.outlook.hit_rate !== null
                  ? ` · ${ai.data.outlook.graded} graded, ${ai.data.outlook.hit_rate.toFixed(0)}% correct`
                  : " · graded after each session"}
              </p>
            </div>
          )}
          {ai.data && ai.data.by_week.length > 0 && (
            <div className="text-xs text-slate-400">
              Weekly hit rate:{" "}
              {ai.data.by_week.map((w) => (
                <span key={w.week} className="mr-3 font-mono">
                  {w.week.slice(5)}: {w.hit_rate.toFixed(0)}% ({w.forecasts})
                </span>
              ))}
            </div>
          )}
          {ai.data && ai.data.lessons.length > 0 && (
            <ol className="list-decimal pl-5 text-xs text-slate-300 space-y-0.5">
              {ai.data.lessons.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ol>
          )}
          {(aiForecasts.data ?? []).length > 0 && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 text-left">
                  <th className="font-normal py-1">Date</th>
                  <th className="font-normal">Stock</th>
                  <th className="font-normal">Forecast</th>
                  <th className="font-normal text-right">P(up)</th>
                  <th className="font-normal text-right">Actual</th>
                  <th className="font-normal text-right">Result</th>
                </tr>
              </thead>
              <tbody>
                {(aiForecasts.data ?? []).map((p) => (
                  <tr key={p.id} className="border-t border-white/5" title={p.reason ?? ""}>
                    <td className="py-1">{p.date}</td>
                    <td className="text-slate-200">{p.name}</td>
                    <td className={p.direction === "up" ? "text-neon-emerald" : p.direction === "down" ? "text-neon-rose" : "text-slate-400"}>
                      {p.direction}
                    </td>
                    <td className="text-right font-mono">{p.probability_up.toFixed(0)}%</td>
                    <td className="text-right font-mono">
                      {p.actual_return_pct !== null ? `${p.actual_return_pct > 0 ? "+" : ""}${p.actual_return_pct.toFixed(1)}%` : "—"}
                    </td>
                    <td className={`text-right ${p.correct === true ? "text-neon-emerald" : p.correct === false ? "text-neon-rose" : "text-slate-500"}`}>
                      {p.graded ? (p.correct ? "right" : "wrong") : "pending"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={queueForecasts} disabled={!ai.data?.llm_available} className="btn-secondary text-xs disabled:opacity-40">
              Forecast now
            </button>
            <button onClick={queueReflection} disabled={!ai.data?.llm_available} className="btn-secondary text-xs disabled:opacity-40">
              Grade & review now
            </button>
            <button onClick={queueOutlook} disabled={!ai.data?.llm_available} className="btn-secondary text-xs disabled:opacity-40">
              Next-session outlook now
            </button>
            <a href={apiService.aiTrainingDataUrl()} className="btn-secondary text-xs" title="Graded forecasts as JSONL, for fine-tuning on a GPU later">
              Export training data
            </a>
            {!ai.data?.llm_available && <span className="text-[11px] text-neon-rose">Local model offline</span>}
          </div>
          {aiMessage && <p className="text-xs text-slate-400">{aiMessage}</p>}
        </section>

        <TradingStyleSection version={version} />

        <ModelEvidenceSection version={version} />

        <section className="space-y-2">
          <h4 className="text-xs uppercase tracking-widest text-slate-400">Strategy library</h4>
          <p className="text-xs text-slate-500">
            Performance learned from graded AiTrading calls and your real trades. Once a strategy has 8+ trades, its win
            rate nudges conviction for new setups of that type (±5 points).
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 text-left">
                <th className="font-normal py-1">Strategy</th>
                <th className="font-normal text-right">Trades</th>
                <th className="font-normal text-right">Win rate</th>
                <th className="font-normal text-right">Avg return</th>
                <th className="font-normal text-right">Real trades</th>
              </tr>
            </thead>
            <tbody>
              {(strategies.data ?? []).map((s) => (
                <tr key={s.strategy} className="border-t border-white/5">
                  <td className="py-1.5 text-slate-200">{s.strategy}</td>
                  <td className="text-right font-mono">{s.trades}</td>
                  <td className="text-right font-mono">{s.win_rate !== null ? `${s.win_rate.toFixed(0)}%` : "learning…"}</td>
                  <td className={`text-right font-mono ${(s.avg_return_pct ?? 0) >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
                    {s.avg_return_pct !== null ? `${s.avg_return_pct > 0 ? "+" : ""}${s.avg_return_pct.toFixed(2)}%` : "—"}
                  </td>
                  <td className="text-right font-mono text-slate-400">{s.real_trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="space-y-2">
          <h4 className="text-xs uppercase tracking-widest text-slate-400">Recent trades & graded calls</h4>
          {(trades.data ?? []).length === 0 ? (
            <p className="text-xs text-slate-500">Nothing yet. Recommendations are graded automatically after their holding period.</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 text-left">
                  <th className="font-normal py-1">Date</th>
                  <th className="font-normal">Stock</th>
                  <th className="font-normal">Source</th>
                  <th className="font-normal">Strategy</th>
                  <th className="font-normal text-right">Qty</th>
                  <th className="font-normal text-right">Entry</th>
                  <th className="font-normal text-right">Exit</th>
                  <th className="font-normal text-right">P&L</th>
                  <th className="font-normal text-right">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {(trades.data ?? []).map((t) => (
                  <tr key={t.id} className="border-t border-white/5">
                    <td className="py-1">{t.execution_date}</td>
                    <td className="text-slate-200">{t.symbol}</td>
                    <td className="text-slate-400">{t.source === "zerodha" ? "Zerodha" : "Auto-graded"}</td>
                    <td className="text-slate-400">{t.strategy ?? "—"}</td>
                    <td className="text-right font-mono">{t.quantity ?? "—"}</td>
                    <td className="text-right font-mono">{t.entry_price.toFixed(2)}</td>
                    <td className="text-right font-mono">{t.exit_price?.toFixed(2) ?? "open"}</td>
                    <td className={`text-right font-mono ${(t.profit_loss ?? 0) >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
                      {t.profit_loss !== null ? t.profit_loss.toLocaleString("en-IN") : "—"}
                    </td>
                    <td className={`text-right ${OUTCOME_STYLES[t.actual_outcome ?? ""] ?? "text-slate-400"}`}>
                      {(t.actual_outcome ?? "open").replace(/_/g, " ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="space-y-2">
          <h4 className="text-xs uppercase tracking-widest text-slate-400">Position sizing</h4>
          <div className="flex items-end gap-3 flex-wrap">
            <label className="text-xs text-slate-400 space-y-1">
              <span className="block">Trading capital (₹)</span>
              <input
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
                inputMode="numeric"
                className="bg-base-800/80 border border-white/10 rounded-lg px-3 py-1.5 text-sm w-40 text-slate-200"
              />
            </label>
            <label className="text-xs text-slate-400 space-y-1">
              <span className="block">Risk per trade (%)</span>
              <input
                value={riskPct}
                onChange={(e) => setRiskPct(e.target.value)}
                inputMode="decimal"
                className="bg-base-800/80 border border-white/10 rounded-lg px-3 py-1.5 text-sm w-24 text-slate-200"
              />
            </label>
            <button onClick={saveRisk} disabled={saving} className="btn-secondary disabled:opacity-50">
              {saving ? "Saving…" : "Save"}
            </button>
            {settings && (
              <span className="text-[11px] text-slate-500">
                Score blend (auto-tuned): technical {(settings.weight_technical * 100).toFixed(0)}% · news{" "}
                {(settings.weight_sentiment * 100).toFixed(0)}% · volume {(settings.weight_volume * 100).toFixed(0)}%
              </span>
            )}
          </div>
          {settingsError && <p className="text-xs text-neon-rose">{settingsError}</p>}
        </section>
      </div>
    </div>
  );
}
