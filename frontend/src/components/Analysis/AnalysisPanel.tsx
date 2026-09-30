import { ReactNode, useEffect, useRef, useState } from "react";
import { useResource } from "../../hooks/useResource";
import apiService from "../../services/api";
import type { AIView, StockAnalysis } from "../../services/types";
import AIConsensusPanel from "./AIConsensusPanel";

interface AnalysisPanelProps {
  symbol: string | null;
  refreshKey: number;
  llmAvailable: boolean;
}

const BREAKDOWN: { key: string; label: string; max: number }[] = [
  { key: "trend", label: "Trend", max: 30 },
  { key: "momentum", label: "Momentum", max: 25 },
  { key: "structure", label: "Structure", max: 20 },
  { key: "volume", label: "Volume", max: 15 },
  { key: "relative_strength", label: "Rel. strength", max: 10 },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1">
      <h4 className="text-[11px] uppercase tracking-widest text-slate-500">{title}</h4>
      {children}
    </section>
  );
}

function Bullets({ items, tone }: { items: string[]; tone?: "good" | "bad" }) {
  const dot = tone === "good" ? "bg-neon-emerald" : tone === "bad" ? "bg-neon-rose" : "bg-slate-500";
  return (
    <ul className="space-y-0.5">
      {items.map((item) => (
        <li key={item} className="text-xs text-slate-300 flex gap-2">
          <span className={`mt-1.5 w-1 h-1 rounded-full shrink-0 ${dot}`} />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function ScoreBars({ analysis }: { analysis: StockAnalysis }) {
  return (
    <div className="grid grid-cols-5 gap-2">
      {BREAKDOWN.map(({ key, label, max }) => {
        const value = analysis.score_breakdown[key] ?? 0;
        return (
          <div key={key} title={`${label}: ${value}/${max}`}>
            <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-neon-blue to-neon-purple"
                style={{ width: `${Math.max(0, Math.min(100, (value / max) * 100))}%` }}
              />
            </div>
            <div className="text-[10px] text-slate-500 mt-0.5 truncate">{label}</div>
          </div>
        );
      })}
    </div>
  );
}

function AIViewSection({ view }: { view: AIView }) {
  const f = view.forecast;
  const tone = !f ? "" : f.direction === "up" ? "text-neon-emerald" : f.direction === "down" ? "text-neon-rose" : "text-slate-300";
  const o = view.outlook;
  return (
    <Section title="AI analyst (background model)">
      {o && (
        <p className="text-xs text-slate-400 mb-1">
          Next session ({o.session_date}): market {o.bias}, {o.probability_up.toFixed(0)}% chance NIFTY closes higher.
          {o.sector_impact ? (
            <span className={o.sector_impact.impact > 0 ? "text-neon-emerald" : o.sector_impact.impact < 0 ? "text-neon-rose" : ""}>
              {" "}Its sector ({o.sector_impact.sector}): {o.sector_impact.impact > 0 ? "+" : ""}
              {o.sector_impact.impact} — {o.sector_impact.reason}
            </span>
          ) : (
            " No sector-specific news impact flagged."
          )}
        </p>
      )}
      {f ? (
        <div className="text-xs text-slate-300 space-y-0.5">
          <p>
            <span className={`font-semibold ${tone}`}>
              {f.direction.toUpperCase()} · {f.probability_up.toFixed(0)}% chance higher
            </span>{" "}
            over {f.horizon_days} trading days
            {f.expected_move_pct !== null && ` (expects ${f.expected_move_pct > 0 ? "+" : ""}${f.expected_move_pct.toFixed(1)}%)`}
            <span className="text-slate-500"> · forecast {f.date}{f.model ? ` by ${f.model}` : ""}, lessons v{f.lessons_version}</span>
          </p>
          {f.reason && <p className="text-slate-400">{f.reason}</p>}
        </div>
      ) : (
        <p className="text-xs text-slate-500">No forecast for this stock yet — the background model forecasts the top 10 and your Manual list each weekday morning.</p>
      )}
      {view.news_reads.length > 0 && (
        <ul className="space-y-0.5 mt-1">
          {view.news_reads.slice(0, 4).map((r) => (
            <li key={r.title} className="text-xs flex gap-2">
              <span className={`font-mono shrink-0 ${r.impact > 0 ? "text-neon-emerald" : r.impact < 0 ? "text-neon-rose" : "text-slate-500"}`}>
                {r.impact > 0 ? `+${r.impact}` : r.impact}
              </span>
              <span className="text-slate-400">
                {r.title}
                {r.reason && <span className="text-slate-500"> — {r.reason}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] text-slate-500 mt-1">
        Track record: {view.track_record}.{" "}
        {view.in_shadow_mode
          ? view.shadow_reason
          : `Earned weight: up to ±${view.trust_weight.toFixed(1)} conviction points.`}
      </p>
    </Section>
  );
}

/** PRD §9: why the AI ranked the selected stock where it did. */
export default function AnalysisPanel({ symbol, refreshKey, llmAvailable }: AnalysisPanelProps) {
  const { data, loading, error } = useResource(
    symbol ? () => apiService.getAnalysis(symbol) : null,
    [symbol, refreshKey],
    true
  );
  const analysis = data && data.symbol === symbol ? data : null;

  const [narrative, setNarrative] = useState<string | null>(null);
  const [narrativeLoading, setNarrativeLoading] = useState(false);
  const [narrativeError, setNarrativeError] = useState<string | null>(null);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;

  useEffect(() => {
    setNarrative(null);
    setNarrativeError(null);
  }, [symbol]);

  const askAi = async () => {
    if (!symbol) return;
    const requested = symbol;
    setNarrativeLoading(true);
    setNarrativeError(null);
    try {
      const result = await apiService.getNarrative(requested);
      if (requested === symbolRef.current) {
        setNarrative(result.narrative);
        if (!result.available) setNarrativeError("The local model is offline.");
      }
    } catch (err) {
      setNarrativeError(err instanceof Error ? err.message : "AI note failed");
    } finally {
      setNarrativeLoading(false);
    }
  };

  return (
    <div className="glass-panel h-full flex flex-col overflow-hidden">
      <div className="px-4 py-2.5 border-b border-white/5 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest">AI Analysis</h2>
        {analysis && (
          <button
            onClick={askAi}
            disabled={narrativeLoading || !llmAvailable}
            title={llmAvailable ? "Ask the local LLM for an analyst note" : "Local LLM offline"}
            className="text-[11px] px-2 py-1 rounded-lg border border-neon-purple/30 text-neon-purple hover:bg-neon-purple/10 disabled:opacity-40"
          >
            {narrativeLoading ? "Writing note…" : "AI analyst note"}
          </button>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
        {!symbol && <div className="text-xs text-slate-500">Select a stock to see why it ranks where it does.</div>}
        {symbol && loading && !analysis && <div className="text-xs text-slate-500">Analyzing…</div>}
        {symbol && error && !analysis && <div className="text-xs text-neon-rose">Analysis unavailable: {error}</div>}

        {analysis && (
          <div className="space-y-3 fade-in">
            <p className="text-sm text-slate-200 leading-relaxed">{analysis.summary}</p>
            <ScoreBars analysis={analysis} />

            {(narrative || narrativeError) && (
              <div className="text-xs text-slate-300 leading-relaxed border-l-2 border-neon-purple/40 pl-3">
                {narrative ?? <span className="text-slate-500">{narrativeError}</span>}
              </div>
            )}

            {analysis.ai_view && <AIViewSection view={analysis.ai_view} />}

            <AIConsensusPanel symbol={analysis.symbol} />

            <Section title="Market context">
              <p className="text-xs text-slate-400 leading-relaxed">{analysis.market_context}</p>
            </Section>

            <Section title="Technical confirmation">
              <Bullets items={analysis.technical_confirmation} tone="good" />
            </Section>

            <Section title="News">
              <p className="text-xs text-slate-400">{analysis.news_sentiment}</p>
              {analysis.news.length > 0 && (
                <ul className="space-y-0.5">
                  {analysis.news.map((h) => (
                    <li key={h.title} className="text-xs flex gap-2">
                      <span className={h.sentiment > 0 ? "text-neon-emerald" : h.sentiment < 0 ? "text-neon-rose" : "text-slate-500"}>
                        {h.sentiment > 0 ? "▲" : h.sentiment < 0 ? "▼" : "•"}
                      </span>
                      {h.link ? (
                        <a href={h.link} target="_blank" rel="noopener noreferrer" className="text-slate-300 hover:text-neon-blue">
                          {h.title}
                        </a>
                      ) : (
                        <span className="text-slate-300">{h.title}</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="Risk factors">
              <Bullets items={analysis.risk_factors} tone="bad" />
            </Section>

            <Section title="Historical comparison">
              <Bullets items={analysis.historical_notes} />
              {analysis.historical.some((h) => h.signals > 0) && (
                <table className="w-full text-[11px] mt-1">
                  <thead>
                    <tr className="text-slate-500 text-left">
                      <th className="font-normal">Setup (1y back-test)</th>
                      <th className="font-normal text-right">Signals</th>
                      <th className="font-normal text-right">Win %</th>
                      <th className="font-normal text-right">Avg</th>
                    </tr>
                  </thead>
                  <tbody>
                    {analysis.historical.map((h) => (
                      <tr key={h.strategy} className={h.strategy === analysis.strategy ? "text-neon-blue" : "text-slate-400"}>
                        <td>{h.strategy}</td>
                        <td className="text-right font-mono">{h.signals}</td>
                        <td className="text-right font-mono">{h.win_rate !== null ? `${h.win_rate.toFixed(0)}%` : "—"}</td>
                        <td className="text-right font-mono">
                          {h.avg_return_pct !== null ? `${h.avg_return_pct > 0 ? "+" : ""}${h.avg_return_pct.toFixed(1)}%` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>

            {analysis.exit_logic.length > 0 && (
              <Section title="Exit logic">
                <Bullets items={analysis.exit_logic} />
              </Section>
            )}

            {Object.keys(analysis.adjustments).length > 0 && (
              <Section title="Evidence adjustments">
                <Bullets
                  items={Object.entries(analysis.adjustments).map(
                    ([label, value]) => `${label}: ${value > 0 ? "+" : ""}${value.toFixed(1)} pts`
                  )}
                />
              </Section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
