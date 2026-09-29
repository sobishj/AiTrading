import { useState } from "react";
import { useResource } from "../../hooks/useResource";
import apiService from "../../services/api";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v.toFixed(0)}%`);

/** Multi-model learning: each model's graded record and the factor statistics measured from real outcomes. */
export default function ModelEvidenceSection({ version }: { version: number }) {
  const perf = useResource(() => apiService.getModelPerformance(), [version], true);
  const [source, setSource] = useState<"history" | "live">("history");
  const patterns = useResource(() => apiService.getPatterns(source), [version, source], true);

  return (
    <>
      <section className="space-y-2">
        <h4 className="text-xs uppercase tracking-widest text-slate-400">Model performance (graded against real prices)</h4>
        {perf.data && perf.data.length === 0 && (
          <p className="text-xs text-slate-500">
            No graded multi-model analyses yet. Each analysis is graded 5 trading days later from actual prices; accuracy per
            model, market regime and setup appears here as outcomes arrive.
          </p>
        )}
        {perf.data && perf.data.length > 0 && (
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-slate-500 text-left">
                <th className="font-normal">Model</th>
                <th className="font-normal text-right">Graded</th>
                <th className="font-normal text-right">Correct</th>
                <th className="font-normal text-right" title="Lower is better (probability calibration)">Brier</th>
                <th className="font-normal text-right" title="Share of its checkable claims the data supported">Evidence</th>
                <th className="font-normal text-right" title="Stated confidence when right / when wrong">Conf. R/W</th>
                <th className="font-normal text-right" title="Hit rate when it disagreed with the combined view">Dissent</th>
                <th className="font-normal text-right">Avg P&L</th>
              </tr>
            </thead>
            <tbody>
              {perf.data.map((m) => (
                <tr key={`${m.profile_id}-${m.model}`} className={m.model === "consensus" ? "text-neon-blue" : "text-slate-300"}>
                  <td className="font-mono truncate max-w-[160px]" title={
                    [...m.by_regime.map((r) => `${r.label}: ${pct(r.hit_rate)} of ${r.n}`),
                     ...m.by_setup.map((r) => `${r.label}: ${pct(r.hit_rate)} of ${r.n}`)].join("\n")}>
                    {m.model}
                  </td>
                  <td className="text-right font-mono">{m.graded}</td>
                  <td className="text-right font-mono">{pct(m.hit_rate)}</td>
                  <td className="text-right font-mono">{m.brier.toFixed(3)}</td>
                  <td className="text-right font-mono">{pct(m.evidence_score_avg)}</td>
                  <td className="text-right font-mono">{pct(m.confidence_when_right)}/{pct(m.confidence_when_wrong)}</td>
                  <td className="text-right font-mono">{m.dissent.n ? `${pct(m.dissent.hit_rate)} of ${m.dissent.n}` : "—"}</td>
                  <td className="text-right font-mono">{m.avg_pnl_pct !== null ? `${m.avg_pnl_pct > 0 ? "+" : ""}${m.avg_pnl_pct.toFixed(2)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="text-[10px] text-slate-600">
          Hover a model for its accuracy by market regime and setup.{" "}
          <a href="/api/ai/training-data/multi" className="text-neon-blue hover:underline">Download the training dataset (JSONL)</a>{" "}
          — every analysis with its exact data, answer, evidence checks and measured outcome.
        </p>
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h4 className="text-xs uppercase tracking-widest text-slate-400 mr-auto">Measured factor evidence (5-day outcomes)</h4>
          {(["history", "live"] as const).map((s) => (
            <button key={s} onClick={() => setSource(s)}
              className={`text-[10px] px-2 py-0.5 rounded border ${source === s ? "border-neon-blue/50 text-neon-blue" : "border-white/10 text-slate-500"}`}>
              {s === "history" ? "Past price history" : "Live analyses"}
            </button>
          ))}
        </div>
        {patterns.data && patterns.data.length === 0 && (
          <p className="text-xs text-slate-500">
            {source === "history"
              ? "The factor study runs automatically (weekly) on ~3 years of your tracked stocks' daily bars."
              : "Builds up as live analyses are graded (at least 30 observations per factor before it counts)."}
          </p>
        )}
        {patterns.data && patterns.data.length > 0 && (
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-slate-500 text-left">
                <th className="font-normal">Factor(s)</th>
                <th className="font-normal">Regime</th>
                <th className="font-normal text-right">Cases</th>
                <th className="font-normal text-right" title="Price moved the way the factor implies">Worked</th>
                <th className="font-normal text-right" title="Same move on any day">Base</th>
                <th className="font-normal text-right">Edge</th>
              </tr>
            </thead>
            <tbody>
              {patterns.data.slice(0, 25).map((r) => (
                <tr key={`${r.pattern}-${r.regime}-${r.bias}`} className="text-slate-300">
                  <td>
                    <span className={r.bias === "bullish" ? "text-neon-emerald" : "text-neon-rose"}>{r.bias === "bullish" ? "▲" : "▼"}</span>{" "}
                    {r.pattern.replace(/_/g, " ")}
                  </td>
                  <td className="text-slate-500">{r.regime}</td>
                  <td className="text-right font-mono">{r.occurrences.toLocaleString("en-IN")}</td>
                  <td className="text-right font-mono">{r.success_rate.toFixed(0)}%</td>
                  <td className="text-right font-mono">{r.base_rate !== null ? `${r.base_rate.toFixed(0)}%` : "—"}</td>
                  <td className={`text-right font-mono ${(r.edge ?? 0) >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
                    {r.edge !== null ? `${r.edge > 0 ? "+" : ""}${r.edge.toFixed(1)}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="text-[10px] text-slate-600">
          Negative edge means the factor was followed by the opposite move more often than usual — the consensus uses that too.
          Measured on the stocks you track today, so it carries survivorship bias; past behaviour is not a guarantee.
        </p>
      </section>
    </>
  );
}
