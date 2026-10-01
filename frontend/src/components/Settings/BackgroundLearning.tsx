import { useEffect, useState } from "react";
import apiService from "../../services/api";
import type { BackgroundStatus } from "../../services/types";
import InfoTip from "../common/InfoTip";

const errorText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  ?? (e instanceof Error ? e.message : "Something went wrong");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString("en-IN", {
  weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : null);

/**
 * Start / stop learning: run in the background from Windows log-in, pause or resume the learning
 * jobs, or run the learning cycle now. Prices and holding alerts are never paused.
 */
export default function BackgroundLearning({ onChanged }: { onChanged: () => void }) {
  const [state, setState] = useState<BackgroundStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => apiService.getBackground().then(setState).catch((e) => setMessage({ ok: false, text: errorText(e) }));
  useEffect(() => { load(); }, []);

  const act = async (key: string, action: () => Promise<BackgroundStatus | void>, done?: string) => {
    setBusy(key);
    setMessage(null);
    try {
      const next = await action();
      if (next) setState(next); else await load();
      if (done) setMessage({ ok: true, text: done });
      onChanged();
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    } finally {
      setBusy(null);
    }
  };

  if (!state) return message ? <p className="text-xs text-neon-rose">{message.text}</p> : null;

  return (
    <section className="glass-panel p-4 space-y-3">
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-semibold mr-auto">
          Background learning
          <InfoTip>Learning runs in the app's backend, not in this browser page: it picks the day's best shares, makes and
            grades forecasts, reads news and NSE filings, practises and rewrites the shared lessons on a schedule — with or
            without this page open. Pausing stops those jobs (and their AI costs); prices and holding alerts
            (stop-loss, target, loss risk) always keep running. Nothing is lost while paused: on resume, everything that
            matured is graded.</InfoTip>
        </h4>
        <span className={`text-[11px] px-2 py-0.5 rounded-md border ${state.paused
          ? "border-amber-300/50 text-amber-300" : "border-neon-emerald/40 text-neon-emerald"}`}>
          {state.paused ? `Paused${state.resume_at ? ` until ${when(state.resume_at)}` : ""}` : "Learning"}
        </span>
      </div>

      <div className="text-[11px] text-slate-400 space-y-0.5">
        <div>Last learning cycle: {when(state.last_learning_at) ?? "not run yet"}</div>
        {!state.paused && state.next_jobs.length > 0 && (
          <div>Next: {state.next_jobs.slice(0, 3).map((j) => `${j.job} — ${when(j.at)}`).join(" · ")}</div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {state.paused ? (
          <button disabled={busy !== null} className="btn-primary text-xs disabled:opacity-50"
            onClick={() => act("resume", () => apiService.setBackground({ pause: "resume" }), "Learning resumed.")}>
            Resume learning
          </button>
        ) : (
          <>
            <button disabled={busy !== null} className="btn-secondary text-xs disabled:opacity-50"
              onClick={() => act("pause", () => apiService.setBackground({ pause: "until_resumed" }), "Learning paused until you resume it.")}>
              Pause learning
            </button>
            <button disabled={busy !== null} className="btn-secondary text-xs disabled:opacity-50"
              onClick={() => act("pause1", () => apiService.setBackground({ pause: "until_tomorrow" }), "Learning paused until 07:30 tomorrow.")}>
              Pause until tomorrow
            </button>
          </>
        )}
        <button disabled={busy !== null} className="btn-secondary text-xs disabled:opacity-50"
          title="Grade everything that has matured now, update statistics and rewrite the shared lessons"
          onClick={() => act("now", () => apiService.runLearningNow(), "Learning cycle started — results appear in Learning in a minute or two.")}>
          {busy === "now" ? "Starting…" : "Learn now"}
        </button>
      </div>

      <label className="flex items-start gap-2 text-xs text-slate-300">
        <input type="checkbox" className="mt-0.5" checked={state.autostart} disabled={busy !== null}
          onChange={(e) => act("auto", () => apiService.setBackground({ autostart: e.target.checked }),
            e.target.checked ? "AiTrading will start in the background whenever you log in to Windows."
              : "AiTrading won't start by itself any more.")} />
        <span>
          Start with Windows, in the background
          <InfoTip>Starts AiTrading invisibly when you log in to Windows and restarts it if it ever stops, so it keeps
            learning without any window or browser open. To stop it completely, run stop-aitrading.bat; it starts again at
            your next log-in while this is on. A sleeping or switched-off PC doesn't learn — missed jobs catch up when it
            wakes.</InfoTip>
          {!state.under_runner && (
            <span className="block text-[10px] text-slate-500">
              This session was started by hand — restart with start-aitrading.bat to get automatic restarts now.
            </span>
          )}
        </span>
      </label>

      {message && <p className={`text-xs ${message.ok ? "text-neon-emerald" : "text-neon-rose"}`}>{message.text}</p>}
    </section>
  );
}
