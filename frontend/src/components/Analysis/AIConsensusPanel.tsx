import { useEffect, useRef, useState } from "react";
import apiService from "../../services/api";
import type { ClaimStatus, Consensus, ConsensusMember } from "../../services/types";

interface AIConsensusPanelProps {
  symbol: string;
}

const signalTone: Record<string, string> = {
  BUY: "text-neon-emerald border-neon-emerald/40 bg-neon-emerald/10",
  SELL: "text-neon-rose border-neon-rose/40 bg-neon-rose/10",
  HOLD: "text-amber-300 border-amber-300/40 bg-amber-300/10",
};

const statusIcon: Record<ClaimStatus, { icon: string; tone: string; title: string }> = {
  SUPPORTED: { icon: "✓", tone: "text-neon-emerald", title: "Supported by the data" },
  NOT_SUPPORTED: { icon: "✗", tone: "text-neon-rose", title: "Contradicted by the data (or news not in the data)" },
  UNKNOWN: { icon: "?", tone: "text-slate-400", title: "The data needed to check this is unavailable" },
  UNVERIFIABLE: { icon: "~", tone: "text-slate-500", title: "Model interpretation — not a checkable fact" },
};

const fmt = (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? "—" : v.toFixed(digits));

function timeAgo(iso?: string | null) {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  return h < 24 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
}

function MemberCard({ m }: { m: ConsensusMember }) {
  const tone = signalTone[m.recommendation === "AVOID" ? "SELL" : m.recommendation ?? "HOLD"] ?? signalTone.HOLD;
  return (
    <div className="glass-panel px-3 py-2 space-y-1.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-slate-200">{m.name ?? m.model}</span>
        <span className="text-[10px] font-mono text-slate-500">{m.model}</span>
        {m.fallback && <span className="text-[10px] text-amber-300">fallback</span>}
        <span className={`text-[10px] font-bold px-1.5 rounded border ${tone}`}>{m.recommendation}</span>
        <span className="text-[11px] text-slate-400">{m.probability_up.toFixed(0)}% up</span>
        {m.stated_confidence !== null && (
          <span className="text-[10px] text-slate-500" title="The model's own stated confidence — shown, but not used as evidence">
            stated confidence {m.stated_confidence.toFixed(0)}
          </span>
        )}
        <span className="ml-auto text-[10px] text-slate-500" title="Weight in the consensus = reliability × evidence">
          weight {fmt(m.weight)}
        </span>
      </div>
      <div className="text-[10px] text-slate-500">
        Reliability: {m.reliability_why ?? "—"} · Evidence: {m.evidence_why ?? "—"}
      </div>
      {m.evidence && m.evidence.checks.length > 0 && (
        <ul className="space-y-0.5">
          {m.evidence.checks.map((c, i) => {
            const s = statusIcon[c.status];
            return (
              <li key={`${c.key}-${i}`} className="text-[11px] flex gap-1.5" title={s.title}>
                <span className={`font-bold shrink-0 w-3 ${s.tone}`}>{s.icon}</span>
                <span className="text-slate-300">
                  {c.label}
                  <span className="text-slate-500"> — data: {c.actual}</span>
                  {c.model_evidence && <span className="text-slate-600"> · model said: “{c.model_evidence}”</span>}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {m.evidence?.flags.map((f) => (
        <p key={f} className="text-[10px] text-amber-300">⚠ {f}</p>
      ))}
      {m.level_issues.map((f) => (
        <p key={f} className="text-[10px] text-amber-300">⚠ Levels: {f}</p>
      ))}
      {(m.technical || m.news || m.reasoning) && (
        <div className="text-[11px] text-slate-400 space-y-0.5 border-l-2 border-white/10 pl-2">
          <p className="text-[9px] uppercase tracking-widest text-slate-600">Model interpretation</p>
          {m.technical && <p>Chart: {m.technical}</p>}
          {m.news && <p>News: {m.news}</p>}
          {m.reasoning && <p>{m.reasoning}</p>}
          {m.risks.length > 0 && <p>Risks: {m.risks.join("; ")}</p>}
        </div>
      )}
      <div className="text-[10px] text-slate-600 flex gap-3 flex-wrap">
        {(m.entry || m.target || m.stop_loss) && (
          <span>Levels (prediction): entry {fmt(m.entry)} · target {fmt(m.target)} · stop {fmt(m.stop_loss)}</span>
        )}
        {m.latency_ms !== null && <span>{(m.latency_ms / 1000).toFixed(1)}s</span>}
        {m.tokens.input + m.tokens.output > 0 && <span>{m.tokens.input + m.tokens.output} tokens</span>}
        {m.outcome && (
          <span className={m.outcome.correct ? "text-neon-emerald" : "text-neon-rose"}>
            Outcome: {m.outcome.return_pct > 0 ? "+" : ""}{m.outcome.return_pct.toFixed(1)}% ({m.outcome.correct ? "right" : "wrong"})
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Multi-model AI analysis: every selected model analyses the same timestamped data,
 * their claims are checked against that data, and the combined view is weighted by
 * verified evidence and measured track record — not by counting votes.
 */
export default function AIConsensusPanel({ symbol }: AIConsensusPanelProps) {
  const [data, setData] = useState<Consensus | null>(null);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [useAll, setUseAll] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;

  useEffect(() => {
    setData(null);
    setError(null);
    setExpanded(false);
    apiService.getConsensus(symbol).then((d) => symbolRef.current === symbol && setData(d)).catch(() => {});
  }, [symbol]);

  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    const t = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(t);
  }, [running]);

  const run = async () => {
    const requested = symbol;
    setRunning(true);
    setElapsed(0);
    setError(null);
    try {
      const result = await apiService.runAIAnalysis(requested, { use_all: useAll, force: true });
      if (symbolRef.current === requested) setData(result);
    } catch (e) {
      if (symbolRef.current === requested) setError(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setRunning(false);
    }
  };

  const c = data?.available ? data : null;
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <h4 className="text-[11px] uppercase tracking-widest text-slate-500">Evidence-checked AI analysis</h4>
        <label className="ml-auto text-[10px] text-slate-400 flex items-center gap-1" title="Ask every enabled model, whatever the analysis mode">
          <input type="checkbox" checked={useAll} onChange={(e) => setUseAll(e.target.checked)} /> Use all enabled models
        </label>
        <button
          onClick={run}
          disabled={running}
          className="text-[11px] px-2 py-1 rounded-lg border border-neon-blue/30 text-neon-blue hover:bg-neon-blue/10 disabled:opacity-40"
        >
          {running ? `Analysing… ${elapsed}s` : c ? "Run again" : "Run AI analysis"}
        </button>
      </div>
      {error && <p className="text-xs text-neon-rose">{error}</p>}
      {data && !data.available && data.failed && data.failed.length > 0 && (
        <p className="text-xs text-neon-rose">No model produced a usable analysis: {data.failed.map((f) => `${f.name}: ${f.error}`).join("; ")}</p>
      )}
      {!c && !running && !error && (
        <p className="text-xs text-slate-500">
          Each selected model analyses the same timestamped market data, news and indicators. Their claims are checked
          against that data, and the result is weighted by verified evidence and each model's measured track record.
        </p>
      )}

      {c && (
        <div className="space-y-2 fade-in">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-sm font-bold px-2 py-0.5 rounded-lg border ${signalTone[c.signal ?? "HOLD"]}`}>{c.signal}</span>
            <span className="text-xs text-slate-300">{c.probability_up?.toFixed(0)}% chance higher in 5 trading days</span>
            <span className="text-[11px] text-slate-400" title="AiTrading's confidence from evidence support, agreement and decisiveness — not any model's own confidence">
              · evidence confidence {c.evidence_confidence?.toFixed(0)}/100
            </span>
            <span className="text-[11px] text-slate-400">· models {c.vote_text}</span>
          </div>

          {c.scores && (
            <div className="grid grid-cols-4 gap-2 text-[10px] text-slate-500">
              <div title="Models' probabilities weighted by evidence × reliability">Models <span className="block text-xs text-slate-300 font-mono">{c.scores.model_probability.toFixed(0)}%</span></div>
              <div title="Measured success of the factors present now (real outcomes)">History <span className="block text-xs text-slate-300 font-mono">{c.scores.history_probability !== null ? `${c.scores.history_probability.toFixed(0)}%` : "n/a"}</span></div>
              <div title="Share of checkable claims supported by the data">Evidence <span className="block text-xs text-slate-300 font-mono">{c.scores.evidence_support.toFixed(0)}%</span></div>
              <div title="Weighted share of models agreeing with the signal">Agreement <span className="block text-xs text-slate-300 font-mono">{c.scores.agreement.toFixed(0)}%</span></div>
            </div>
          )}

          {(c.evidence_for?.length ?? 0) > 0 && (
            <ul className="space-y-0.5">
              {c.evidence_for!.map((e) => (
                <li key={e.key} className="text-xs text-slate-300 flex gap-1.5">
                  <span className="text-neon-emerald">✓</span>
                  <span>{e.label} <span className="text-slate-500">— {e.actual} · cited by {e.cited_by.join(", ")}</span></span>
                </li>
              ))}
            </ul>
          )}
          {(c.risks?.length ?? 0) > 0 && (
            <ul className="space-y-0.5">
              {c.risks!.map((e) => (
                <li key={e.key} className="text-xs text-slate-300 flex gap-1.5">
                  <span className="text-amber-300">⚠</span>
                  <span>{e.label} <span className="text-slate-500">— {e.actual}</span></span>
                </li>
              ))}
            </ul>
          )}

          {(c.historical?.length ?? 0) > 0 && (
            <div className="text-[11px] text-slate-400 space-y-0.5">
              <p className="text-[10px] uppercase tracking-widest text-slate-600">Historical evidence (measured outcomes)</p>
              {c.historical!.map((h) => (
                <p key={h.pattern}>
                  {h.pattern.replace(/_/g, " ")}: price {h.bias === "bullish" ? "rose" : "fell"} in {h.success_rate.toFixed(0)}% of{" "}
                  {h.occurrences.toLocaleString("en-IN")} cases vs {h.base_rate.toFixed(0)}% on any day
                  <span className={h.edge >= 0 ? "text-neon-emerald" : "text-neon-rose"}> ({h.edge > 0 ? "+" : ""}{h.edge.toFixed(1)} pts)</span>
                  <span className="text-slate-600"> · {h.source}</span>
                </p>
              ))}
            </div>
          )}

          {c.levels?.source && (
            <p className="text-[11px] text-slate-400">
              Levels ({c.levels.source}): entry {fmt(c.levels.entry)} · target {fmt(c.levels.target)} · stop {fmt(c.levels.stop_loss)}
            </p>
          )}

          {(c.disagreement?.length ?? 0) > 0 && (
            <div className="text-[11px] text-slate-400 space-y-0.5">
              <p className="text-[10px] uppercase tracking-widest text-slate-600">Disagreement</p>
              {c.disagreement!.map((d) => (
                <p key={d.model}>
                  <span className="text-slate-300">{d.model}</span> says {d.recommendation}
                  {d.supported.length > 0 && <span> — backed by data: {d.supported.join(", ")}</span>}
                  {d.not_supported.length > 0 && <span className="text-neon-rose"> — contradicted: {d.not_supported.join(", ")}</span>}
                </p>
              ))}
            </div>
          )}

          <ul className="space-y-0.5">
            {c.reasoning?.map((r) => (
              <li key={r} className="text-[11px] text-slate-500">• {r}</li>
            ))}
          </ul>

          <button onClick={() => setExpanded(!expanded)} className="text-[11px] text-neon-blue hover:underline">
            {expanded ? "▾" : "▸"} Individual AI analysis ({c.members?.length ?? 0}
            {c.failed && c.failed.length > 0 ? `, ${c.failed.length} failed` : ""})
          </button>
          {expanded && (
            <div className="space-y-1.5">
              {c.members?.map((m) => <MemberCard key={`${m.profile_id}-${m.model}`} m={m} />)}
              {c.failed?.map((f) => (
                <p key={`${f.name}-${f.error}`} className="text-[11px] text-neon-rose">✗ {f.name} ({f.model}): {f.error}</p>
              ))}
            </div>
          )}

          <p className="text-[10px] text-slate-600">
            Analysed {timeAgo(c.created_at)} · price data {c.data_timestamp ?? "UNKNOWN"} via {c.data_source ?? "—"} · regime{" "}
            {c.market_regime ?? "UNKNOWN"}. {c.disclaimer}
          </p>
        </div>
      )}
    </section>
  );
}
