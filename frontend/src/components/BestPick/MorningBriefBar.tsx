import { useState } from "react";
import type { MarketOutlook, MorningBrief } from "../../services/types";
import Markdown from "../Chat/Markdown";

interface MorningBriefBarProps {
  brief: MorningBrief | null;
  outlook: MarketOutlook | null;
  loading: boolean;
  onSelect: (symbol: string) => void;
}

const REGIME_STYLES: Record<string, string> = {
  "risk-on": "text-neon-emerald border-neon-emerald/30 bg-neon-emerald/10",
  neutral: "text-neon-blue border-neon-blue/30 bg-neon-blue/10",
  "risk-off": "text-neon-rose border-neon-rose/30 bg-neon-rose/10",
};

/** PRD §13: today's best trade in one line above the chart; the full brief opens on demand. */
const BIAS_STYLES: Record<string, string> = {
  up: "text-neon-emerald border-neon-emerald/30 bg-neon-emerald/10",
  down: "text-neon-rose border-neon-rose/30 bg-neon-rose/10",
  flat: "text-slate-300 border-white/15 bg-white/5",
};

function OutlookDetails({ outlook }: { outlook: MarketOutlook }) {
  return (
    <div className="space-y-2">
      <h4 className="text-[11px] uppercase tracking-widest text-slate-500">
        AI next-session outlook · {outlook.session_date}
      </h4>
      <p className="text-sm text-slate-200">
        <span className={`pill border mr-2 ${BIAS_STYLES[outlook.bias] ?? BIAS_STYLES.flat}`}>{outlook.bias.toUpperCase()}</span>
        {outlook.probability_up.toFixed(0)}% chance NIFTY closes higher. {outlook.summary}
      </p>
      {outlook.sector_impacts.length > 0 && (
        <ul className="text-xs space-y-0.5">
          {outlook.sector_impacts.map((s) => (
            <li key={s.sector} className="flex gap-2">
              <span className={`font-mono w-6 ${s.impact > 0 ? "text-neon-emerald" : s.impact < 0 ? "text-neon-rose" : "text-slate-500"}`}>
                {s.impact > 0 ? `+${s.impact}` : s.impact}
              </span>
              <span className="text-slate-300 w-28 shrink-0">{s.sector}</span>
              <span className="text-slate-500">{s.reason}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] text-slate-500">
        Based on {outlook.headlines_considered} headlines, global markets and FII/DII flows. Generated{" "}
        {new Date(outlook.generated_at + (outlook.generated_at.endsWith("Z") ? "" : "Z")).toLocaleString("en-IN")}.
        {outlook.correct !== null && ` Result: NIFTY ${outlook.actual_return_pct! > 0 ? "+" : ""}${outlook.actual_return_pct?.toFixed(2)}% — ${outlook.correct ? "right" : "wrong"}.`}
      </p>
    </div>
  );
}

export default function MorningBriefBar({ brief, outlook, loading, onSelect }: MorningBriefBarProps) {
  const [open, setOpen] = useState(false);

  if (!brief) {
    return (
      <div className="glass-panel px-4 py-2 text-xs text-slate-500">
        {loading ? "Preparing today's brief…" : "Morning brief unavailable."}
      </div>
    );
  }

  const plan = brief.best_plan;
  const regimeClass = REGIME_STYLES[brief.regime ?? "neutral"] ?? REGIME_STYLES.neutral;
  const generated = new Date(brief.generated_at + (brief.generated_at.endsWith("Z") ? "" : "Z"));

  return (
    <>
      <div className="glass-panel px-4 py-2 flex items-center gap-3 border-neon-emerald/15 min-w-0">
        <span className="pill bg-neon-emerald/10 text-neon-emerald border border-neon-emerald/30 shrink-0">
          Morning Pick
        </span>
        {plan ? (
          <button
            onClick={() => onSelect(plan.symbol)}
            className="flex items-baseline gap-2 min-w-0 text-left hover:opacity-90"
            title="Open this stock"
          >
            <span className="font-semibold neon-text-emerald shrink-0">{plan.name}</span>
            <span className="text-xs text-slate-400 truncate">
              {plan.action} · {plan.strategy} · entry ₹{plan.entry_low.toLocaleString("en-IN")}–
              {plan.entry_high.toLocaleString("en-IN")} · target ₹{plan.target.toLocaleString("en-IN")} · stop ₹
              {plan.stop_loss.toLocaleString("en-IN")} · 1:{plan.risk_reward.toFixed(1)}
            </span>
          </button>
        ) : (
          <span className="text-xs text-slate-400 truncate">No stock meets the buy criteria today — standing aside is a position too.</span>
        )}
        {outlook && (
          <button
            onClick={() => setOpen(true)}
            title={`AI outlook for ${outlook.session_date}: ${outlook.summary ?? ""}`}
            className={`pill border shrink-0 ml-auto ${BIAS_STYLES[outlook.bias] ?? BIAS_STYLES.flat}`}
          >
            Next session: {outlook.bias === "up" ? "▲" : outlook.bias === "down" ? "▼" : "▬"} {outlook.probability_up.toFixed(0)}%
          </button>
        )}
        <span className={`pill border shrink-0 ${outlook ? "" : "ml-auto"} ${regimeClass}`}>{brief.regime ?? "neutral"}</span>
        <button onClick={() => setOpen(true)} className="text-xs text-slate-400 hover:text-neon-blue shrink-0">
          Read brief
        </button>
      </div>

      {open && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6" onClick={() => setOpen(false)}>
          <div className="glass-panel bg-base-900/95 max-w-2xl w-full max-h-[80vh] overflow-y-auto p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold">Morning Brief · {brief.brief_date}</h3>
              <button onClick={() => setOpen(false)} className="text-slate-500 hover:text-slate-200" aria-label="Close">
                ✕
              </button>
            </div>
            <p className="text-[11px] text-slate-500">Generated {generated.toLocaleString("en-IN")}</p>
            {outlook && <OutlookDetails outlook={outlook} />}
            {brief.ai_brief && (
              <div className="text-sm text-slate-200 leading-relaxed border-l-2 border-neon-purple/40 pl-3">
                <Markdown text={brief.ai_brief} />
              </div>
            )}
            <div className="text-sm text-slate-300 leading-relaxed whitespace-pre-line">{brief.brief_text}</div>
            {brief.watchlist_top.length > 0 && (
              <div>
                <h4 className="text-[11px] uppercase tracking-widest text-slate-500 mb-1">Top of the watchlist</h4>
                <ul className="text-sm space-y-1">
                  {brief.watchlist_top.map((w) => (
                    <li key={w.symbol}>
                      <button
                        className="text-slate-300 hover:text-neon-blue"
                        onClick={() => {
                          onSelect(w.symbol);
                          setOpen(false);
                        }}
                      >
                        {w.name} — {w.action} {w.strategy !== "No Setup" ? `· ${w.strategy}` : ""} · {w.conviction.toFixed(0)}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {brief.headlines.length > 0 && (
              <div>
                <h4 className="text-[11px] uppercase tracking-widest text-slate-500 mb-1">Headlines</h4>
                <ul className="text-xs text-slate-400 space-y-0.5 list-disc pl-5">
                  {brief.headlines.map((h) => (
                    <li key={h}>{h}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
