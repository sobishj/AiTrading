import { useEffect, useState } from "react";
import apiService from "../../services/api";
import type { GeneralSettings } from "../../services/types";
import InfoTip from "../common/InfoTip";
import BackgroundLearning from "./BackgroundLearning";

type Values = Omit<GeneralSettings, "defaults">;

const errorText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  ?? (e instanceof Error ? e.message : "Something went wrong");

/** Settings -> General: how the daily pick works. Saved in the database, so a backup carries them. */
export default function GeneralTab({ onChanged = () => {} }: { onChanged?: () => void }) {
  const [saved, setSaved] = useState<GeneralSettings | null>(null);
  const [form, setForm] = useState<Values | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiService.getGeneralSettings().then((g) => { setSaved(g); setForm(g); })
      .catch((e) => setMessage({ ok: false, text: errorText(e) }));
  }, []);

  if (!form || !saved) return message ? <p className="text-xs text-neon-rose">{message.text}</p>
    : <p className="text-xs text-slate-500">Loading…</p>;

  const save = async (values: Partial<Values>) => {
    setBusy(true);
    try {
      const g = await apiService.saveGeneralSettings(values);
      setSaved(g); setForm(g);
      setMessage({ ok: true, text: "Saved — used from the next pick." });
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  const input = "w-full bg-base-800/80 border border-white/10 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-neon-blue/50";
  const field = (key: keyof Values, label: string, tip: string, type: "number" | "time" = "number") => (
    <label className="text-xs text-slate-400 space-y-1">
      <span className="block">{label}<InfoTip>{tip}</InfoTip></span>
      <input type={type} value={String(form[key])} className={input}
        onChange={(e) => setForm({ ...form, [key]: type === "number" ? Number(e.target.value) : e.target.value })} />
      <span className="block text-[10px] text-slate-600">Default {String(saved.defaults[key])}</span>
    </label>
  );
  const changed = (Object.keys(form) as (keyof Values)[]).some((k) => k !== ("defaults" as never) && form[k] !== saved[k]);

  return (
    <div className="space-y-5">
      <BackgroundLearning onChanged={onChanged} />
      <section className="space-y-3">
        <h4 className="text-xs uppercase tracking-widest text-slate-400">Daily pick of the best shares</h4>
        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input type="checkbox" checked={form.discovery_enabled}
            onChange={(e) => setForm({ ...form, discovery_enabled: e.target.checked })} />
          Pick the Auto list from the whole NIFTY 500 every weekday
          <InfoTip>Off: the Auto list stays as it is (you can still press "Find today's best now" in the Auto tab).</InfoTip>
        </label>
        <div className="grid md:grid-cols-2 gap-3">
          {field("universe_screen_time", "Pick time (IST, weekdays)",
            "When the daily pick runs. It must finish before the AI forecasts (08:45), so 08:00–08:30 works best.", "time")}
          {field("auto_list_size", "Shares in the Auto list",
            "How many of the day's best shares the Auto list keeps (10–200). More shares means more news reads by the AI.")}
          {field("ai_review_shortlist", "Shares the AI models review",
            "The best N screened shares every enabled model analyses (0–50). Each costs one analysis per paid model, so this is the main cost lever of the daily pick.")}
          {field("min_traded_value_cr", "Minimum daily traded value (₹ crore)",
            "Shares trading less than this per day (median of 20 sessions) are skipped as too illiquid to enter and exit cleanly.")}
        </div>
      </section>
      {message && <p className={`text-xs ${message.ok ? "text-neon-emerald" : "text-neon-rose"}`}>{message.text}</p>}
      <div className="flex gap-2 justify-end">
        <button disabled={busy} className="btn-secondary text-sm disabled:opacity-50"
          onClick={() => save(saved.defaults)}>Reset to defaults</button>
        <button disabled={busy || !changed} className="btn-primary text-sm disabled:opacity-50"
          onClick={() => save(form)}>{busy ? "Saving…" : "Save"}</button>
      </div>
      <p className="text-[11px] text-slate-500">These settings are stored in the database, so they move with a backup.</p>
    </div>
  );
}
