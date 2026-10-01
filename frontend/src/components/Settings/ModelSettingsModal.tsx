import { useEffect, useState } from "react";
import apiService from "../../services/api";
import InfoTip from "../common/InfoTip";
import type { LLMPreset, LLMProfile, LLMProfilesResponse, ModelCostStats, ModelCredit, ModelPresets, ModelSpend, ModelUsage, ProviderKind } from "../../services/types";
import CostEstimate from "./CostEstimate";

const RETRY_MS = 3000;

interface ModelSettingsModalProps {
  onClose: () => void;
  onChanged: () => void;
  /** Rendered inside the Settings window (no overlay or title of its own). */
  embedded?: boolean;
}

interface Draft {
  id?: number;
  name: string;
  kind: ProviderKind;
  base_url: string;
  api_key: string;
  model: string;
  daily_limit: number;
  allow_practice: boolean;
  notes?: string;
  has_key?: boolean;
  enabled: boolean;
  priority: number;
  temperature: string;
  max_tokens: string;
  timeout_s: string;
  hourly_limit: number;
  input_price: string;
  output_price: string;
}

const num = (v: string) => (v.trim() === "" ? null : Number(v));
const str = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));

const CREDIT_STYLE: Record<ModelCredit["status"], string> = {
  local: "text-neon-emerald bg-neon-emerald/10",
  balance: "text-neon-emerald bg-neon-emerald/10",
  credit_ok: "text-neon-emerald bg-neon-emerald/10",
  no_credit: "text-neon-rose bg-neon-rose/10",
  unknown: "text-slate-400 bg-white/5",
  error: "text-amber-300 bg-amber-300/10",
};

/** Paid models whose balance can't be read: a tiny real request is the only way to confirm credit. */
const needsCheck = (c?: ModelCredit) => !!c && ["unknown", "credit_ok", "no_credit"].includes(c.status);

const input =
  "w-full bg-base-800/80 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-neon-blue/50";

/**
 * Switch AI models: saved connections to local servers (Bionic, LM Studio,
 * Ollama) or cloud APIs (Claude, Kimi, OpenAI, OpenRouter, Gemini), and which
 * one serves chat vs. the 24/7 background work.
 */
export default function ModelSettingsModal({ onClose, onChanged, embedded = false }: ModelSettingsModalProps) {
  const [data, setData] = useState<LLMProfilesResponse | null>(null);
  const [presets, setPresets] = useState<LLMPreset[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [choosing, setChoosing] = useState(false);   // the "Add a model" provider picker is open
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [desktop, setDesktop] = useState(true);
  const [status, setStatus] = useState<Record<number, boolean>>({});
  const [usage, setUsage] = useState<Record<number, ModelUsage>>({});
  const [presetsInfo, setPresetsInfo] = useState<ModelPresets | null>(null);
  const [presetName, setPresetName] = useState("");
  const [credit, setCredit] = useState<Record<number, ModelCredit>>({});
  const [spend, setSpend] = useState<Record<number, ModelSpend>>({});
  const [costStats, setCostStats] = useState<Record<number, ModelCostStats>>({});
  // Why the model list couldn't be loaded (null while loading or once loaded).
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadExtras = () => {
    apiService.getProviderStatus().then((rows) => setStatus(Object.fromEntries(rows.map((r) => [r.id, r.connected])))).catch(() => {});
    apiService.getModelUsage().then((rows) => setUsage(Object.fromEntries(rows.map((r) => [r.profile_id, r])))).catch(() => {});
    apiService.getModelPresets().then(setPresetsInfo).catch(() => {});
    apiService.getModelSpend().then((rows) => setSpend(Object.fromEntries(rows.map((r) => [r.profile_id, r])))).catch(() => {});
    apiService.getCostStats().then((rows) => setCostStats(Object.fromEntries(rows.map((r) => [r.profile_id, r])))).catch(() => {});
    apiService.getModelCredit().then((rows) => setCredit(Object.fromEntries(rows.map((r) => [r.profile_id, r])))).catch(() => {});
  };

  const checkCredit = async (p: LLMProfile) => {
    setBusy(`credit-${p.id}`);
    setMessage(null);
    try {
      const c = await apiService.checkModelCredit(p.id);
      setCredit((prev) => ({ ...prev, [p.id]: c }));
      if (c.error) setMessage({ ok: false, text: `${p.name}: ${c.error}` });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Credit check failed" });
    } finally {
      setBusy(null);
    }
  };

  const money = (period: ModelSpend["today"]) =>
    period.usd === null ? `${(period.input_tokens + period.output_tokens).toLocaleString("en-IN")} tokens`
      : `$${period.usd.toFixed(2)}${period.inr !== null ? ` (₹${period.inr.toLocaleString("en-IN", { maximumFractionDigits: 0 })})` : ""}`;

  const spendLine = (p: LLMProfile) => {
    const sp = spend[p.id];
    if (!sp || sp.is_local) return null;
    const month = new Date().toLocaleString("en-IN", { month: "long" });
    const title = [
      "Spent by AiTrading only (tokens the provider reported × price). Other apps using the same key aren't included.",
      sp.price ? `Price: $${sp.price.input}/$${sp.price.output} per 1M tokens in/out (${sp.price.source}).`
        : "No price known for this model — enter prices on the model (Edit) to see the cost.",
      sp.fx ? `₹ at USD/INR ${sp.fx.rate.toFixed(2)} (Yahoo, ${new Date(sp.fx.as_of).toLocaleDateString("en-IN")}).` : "",
    ].filter(Boolean).join("\n");
    return (
      <div className="text-[11px] text-slate-400" title={title}>
        AiTrading spend: today {money(sp.today)} · {month} {money(sp.month)}
        {!sp.price && <span className="text-amber-300"> · set prices to see cost</span>}
      </div>
    );
  };

  const creditBadge = (p: LLMProfile) => {
    const c = credit[p.id];
    if (!c) return null;
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className={`text-[10px] px-1.5 rounded ${CREDIT_STYLE[c.status]}`}
          title={[c.message, c.note, c.checked_at ? `Last seen: ${new Date(c.checked_at).toLocaleString("en-IN")}` : ""].filter(Boolean).join("\n")}>
          {c.label}
        </span>
        {needsCheck(c) && (
          <button onClick={() => checkCredit(p)} disabled={busy !== null}
            title="Sends one tiny request (about a cent at most) to confirm the account has credit"
            className="text-[10px] text-neon-blue hover:underline disabled:opacity-40">
            {busy === `credit-${p.id}` ? "Checking…" : "Check"}
          </button>
        )}
      </span>
    );
  };
  /** Resolves true once the model list loaded; false (with the reason shown) if the backend didn't answer. */
  const load = (): Promise<boolean> => {
    loadExtras();
    return apiService.getLLMProfiles()
      .then((d) => { setData(d); setLoadError(null); return true; })
      .catch((e) => {
        const text = String(e?.message ?? e);
        setLoadError(text);
        setMessage({ ok: false, text });
        return false;
      });
  };

  const loadStatic = () => {
    apiService.getLLMPresets().then(setPresets).catch(() => {});
    apiService.getSettings().then((s) => setDesktop(s.desktop_notifications ?? true)).catch(() => {});
  };

  useEffect(() => {
    // The backend may be starting, restarting or stopped when the modal opens. Keep retrying
    // until the model list loads instead of leaving empty dropdowns with no explanation.
    let cancelled = false;
    let timer: number | undefined;
    const attempt = () => {
      loadStatic();
      load().then((ok) => {
        if (cancelled) return;
        if (ok) setMessage(null);
        else timer = window.setTimeout(attempt, RETRY_MS);
      });
    };
    attempt();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, []);

  const byId = (id: number | null) => data?.profiles.find((p) => p.id === id);

  const setActive = async (role: "chat" | "background", id: number) => {
    if (!data) return;
    const chat = role === "chat" ? id : data.chat_profile_id!;
    const background = role === "background" ? id : data.background_profile_id!;
    const previous = data;
    // Show the choice at once; reachability is re-checked in the background (slow when a local server is off).
    setData({ ...data, chat_profile_id: chat, background_profile_id: background });
    setMessage(null);
    try {
      await apiService.setActiveModels(chat, background);
      onChanged();
      load();
    } catch (e) {
      setData(previous);
      setMessage({ ok: false, text: `Could not switch the model: ${e instanceof Error ? e.message : "backend not reachable"}` });
    }
  };

  const setPractice = async (id: number | null) => {
    if (!data) return;
    const previous = data;
    setData({ ...data, practice_profile_id: id });
    try {
      await apiService.setActiveModels(data.chat_profile_id!, data.background_profile_id!, id);
      onChanged();
      load();
    } catch (e) {
      setData(previous);
      setMessage({ ok: false, text: `Could not switch the practice model: ${e instanceof Error ? e.message : "backend not reachable"}` });
    }
  };

  const startNew = (preset: LLMPreset) => {
    setChoosing(false);
    setModels([]);
    setMessage(null);
    setDraft({ name: preset.name, kind: preset.kind, base_url: preset.base_url, api_key: preset.api_key,
      model: preset.model, daily_limit: preset.daily_limit, allow_practice: preset.allow_practice, notes: preset.notes,
      enabled: true, priority: 100, temperature: "", max_tokens: "", timeout_s: "", hourly_limit: 0,
      input_price: "", output_price: "" });
  };

  const startEdit = (p: LLMProfile) => {
    setModels([]);
    setMessage(null);
    setDraft({ id: p.id, name: p.name, kind: p.kind, base_url: p.base_url ?? "", api_key: "", model: p.model,
      daily_limit: p.daily_limit, allow_practice: p.allow_practice, has_key: p.has_key,
      enabled: p.enabled, priority: p.priority, temperature: str(p.temperature), max_tokens: str(p.max_tokens),
      timeout_s: str(p.timeout_s), hourly_limit: p.hourly_limit, input_price: str(p.input_price),
      output_price: str(p.output_price) });
  };

  const probe = { kind: draft?.kind ?? "openai_compatible", base_url: draft?.base_url || null,
    api_key: draft?.api_key || null, profile_id: draft?.id ?? null } as const;

  const fetchModels = async () => {
    setBusy("models");
    setMessage(null);
    try {
      const list = await apiService.listProviderModels(probe);
      setModels(list);
      setMessage({ ok: true, text: `${list.length} models available.` });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Could not list models" });
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    if (!draft?.model) return setMessage({ ok: false, text: "Choose a model first." });
    setBusy("test");
    setMessage(null);
    try {
      const r = await apiService.testProvider({ ...probe, model: draft.model });
      setMessage(r.ok ? { ok: true, text: `Works — replied "${r.reply}" in ${r.seconds}s.` } : { ok: false, text: r.error ?? "Failed" });
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    if (!draft) return;
    if (!draft.name || !draft.model) return setMessage({ ok: false, text: "Name and model are required." });
    setBusy("save");
    try {
      await apiService.saveLLMProfile({
        name: draft.name, kind: draft.kind, base_url: draft.base_url || null, api_key: draft.api_key || null,
        model: draft.model, daily_limit: draft.daily_limit, allow_practice: draft.allow_practice,
        enabled: draft.enabled, priority: draft.priority, temperature: num(draft.temperature),
        max_tokens: num(draft.max_tokens), timeout_s: num(draft.timeout_s), hourly_limit: draft.hourly_limit,
        input_price: num(draft.input_price), output_price: num(draft.output_price),
      }, draft.id);
      setDraft(null);
      await load();
      onChanged();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Could not save" });
    } finally {
      setBusy(null);
    }
  };

  const remove = async (p: LLMProfile) => {
    try {
      await apiService.deleteLLMProfile(p.id);
      await load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Could not delete" });
    }
  };

  const toggleEnabled = async (p: LLMProfile) => {
    if (data) setData({ ...data, profiles: data.profiles.map((x) => (x.id === p.id ? { ...x, enabled: !p.enabled } : x)) });
    try {
      await apiService.saveLLMProfile({ name: p.name, kind: p.kind, base_url: p.base_url, api_key: null, model: p.model,
        daily_limit: p.daily_limit, allow_practice: p.allow_practice, enabled: !p.enabled }, p.id);
      await load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Could not update" });
      load();   // put the tick back to what is actually saved
    }
  };

  const setMode = async (mode: "single" | "multi") => {
    await apiService.setAnalysisMode(mode);
    loadExtras();
    onChanged();
  };

  const presetAction = async (action: () => Promise<void>, done: string) => {
    setBusy("preset");
    try {
      await action();
      await load();
      onChanged();
      setMessage({ ok: true, text: done });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Preset failed" });
    } finally {
      setBusy(null);
    }
  };

  const toggleDesktop = async () => {
    await apiService.setDesktopNotifications(!desktop);
    setDesktop(!desktop);
  };

  const renderRole = (role: "chat" | "background", label: string, hint: string) => {
    const current = role === "chat" ? data?.chat_profile_id : data?.background_profile_id;
    const available = role === "chat" ? data?.chat_available : data?.background_available;
    const currentProfile = byId(current ?? null);
    return (
      <label key={role} className="block space-y-1">
        <span className="text-xs text-slate-400 flex items-center gap-2">
          {label}
          <span className={`w-1.5 h-1.5 rounded-full ${available ? "bg-neon-emerald" : "bg-neon-rose"}`} />
          <span className="text-[10px]">{available ? "reachable" : "not reachable"}</span>
        </span>
        <select value={current ?? ""} onChange={(e) => setActive(role, Number(e.target.value))} className={input}>
          {data?.profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} — {p.model}{credit[p.id] ? ` · ${credit[p.id].status === "no_credit" ? "⚠ " : ""}${credit[p.id].label}` : ""}
            </option>
          ))}
        </select>
        {currentProfile && credit[currentProfile.id] && (
          <span className="flex items-center gap-1 text-[10px] text-slate-500">Credit: {creditBadge(currentProfile)}</span>
        )}
        {currentProfile && spendLine(currentProfile)}
        <span className="block text-[10px] text-slate-500">{hint}</span>
      </label>
    );
  };

  const bgProfile = byId(data?.background_profile_id ?? null);
  const practiceProfile = byId(data?.practice_profile_id ?? null) ?? bgProfile;

  const content = (
      <div className={embedded ? "space-y-5" : "glass-panel bg-base-900/95 max-w-3xl w-full max-h-[88vh] overflow-y-auto p-6 space-y-5"}
        onClick={(e) => e.stopPropagation()}>
        {!embedded && (
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold">AI models</h3>
            <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Close">✕</button>
          </div>
        )}

        {!data && (
          <div className={`rounded-lg border px-3 py-2 text-xs ${loadError ? "border-neon-rose/40 text-neon-rose" : "border-slate-600 text-slate-400"}`}>
            {loadError
              ? <>Can't load your models — the AiTrading backend isn't answering ({loadError}). It may be starting up or
                  stopped; run start-aitrading.bat if it isn't running. Retrying every {RETRY_MS / 1000} s…</>
              : "Loading your models…"}
          </div>
        )}

        <section className="grid md:grid-cols-2 gap-4">
          {renderRole("chat", "Chat & analyst notes", "Answers your questions. A strong cloud model (e.g. Claude) helps most here.")}
          {renderRole("background", "Background work (24/7)",
            "News reads, daily forecasts, outlooks, lessons and chart practice. Many calls — a free local model is usually best.")}
        </section>
        {data && presetsInfo && (() => {
          // The dropdowns pick one model per job; stock analysis may use several. Say who, so the
          // single name in "Background work" isn't read as the only model analysing stocks.
          const team = presetsInfo.current.mode === "multi"
            ? data.profiles.filter((p) => p.enabled).sort((a, b) => a.priority - b.priority)
            : bgProfile ? [bgProfile] : [];
          return (
            <p className="text-[11px] text-slate-400 -mt-2">
              <span className="text-slate-300 font-medium">Stock analysis</span> (daily forecasts and trade calls):{" "}
              {team.length ? team.map((p) => p.name).join(" + ") : "no model enabled"}
              {presetsInfo.current.mode === "multi" ? " — every ticked model below, combined" : " — the background model (single-model mode)"}.
              <InfoTip>The two dropdowns choose one model for jobs that always use a single model: chat, and background
                jobs (news reads, market outlooks, writing the shared lessons, chart practice). Stock analysis is
                separate: in Multi-model mode every ticked model under Saved models analyses each stock independently,
                and the answers are combined by evidence and measured accuracy.</InfoTip>
            </p>
          );
        })()}
        {data && (
          <label className="block space-y-1 text-xs text-slate-400">
            <span className="flex items-center gap-1">
              Chart practice (market closed: nights &amp; weekends)
              <InfoTip>Practice replays old charts with the date hidden; each call is graded at once against what really
                happened, and the results feed the shared lessons every model reads. It makes hundreds of calls a night, so
                a free local model is the natural choice — the background model can stay a paid one. Practice never earns a
                model any weight in the rankings (it has no news and could be subtly optimistic); only live forecasts do.</InfoTip>
            </span>
            <select value={data.practice_profile_id ?? ""} className={input}
              onChange={(e) => setPractice(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Same as background work{bgProfile ? ` (${bgProfile.name})` : ""}</option>
              {data.profiles.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.model}</option>)}
            </select>
            {practiceProfile && !practiceProfile.allow_practice && (
              <span className="block text-[11px] text-amber-300">Practice is off: {practiceProfile.name} isn't allowed to
                practise (hundreds of calls a day). Pick a local model here, or tick "Allow chart practice" on it.</span>
            )}
          </label>
        )}

        <section className="glass-panel p-3 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-xs uppercase tracking-widest text-slate-400 mr-auto">Stock analysis</h4>
            {(["single", "multi"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)}
                className={`text-[11px] px-2 py-1 rounded-lg border ${presetsInfo?.current.mode === m
                  ? "border-neon-blue/50 text-neon-blue bg-neon-blue/10" : "border-white/10 text-slate-400 hover:text-slate-200"}`}>
                {m === "single" ? "Single model (background model)" : "Multi-model (all enabled)"}
              </button>
            ))}
          </div>
          <p className="text-[10px] text-slate-500">
            Multi-model: each enabled model analyses the daily top 10, your Manual list and holdings independently; claims are
            checked against the data and combined by evidence and measured accuracy. News reads, practice and chat stay on
            their own models. Which setup is more accurate is measured over time (Learning → model performance), not assumed.
          </p>
          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="text-[11px] text-slate-400 mr-1">Presets:</span>
            {presetsInfo?.builtin.map((b) => (
              <button key={b.key} disabled={busy !== null} title={`${b.description}\nEnables: ${b.enabled.join(", ") || "—"}`}
                onClick={() => presetAction(() => apiService.applyBuiltinPreset(b.key), `${b.name} applied.`)}
                className="text-[11px] px-2 py-1 rounded-full border border-white/10 text-slate-300 hover:border-neon-blue/40 hover:text-neon-blue disabled:opacity-50">
                {b.name}
              </button>
            ))}
            {presetsInfo?.saved.map((sp) => (
              <span key={sp.id} className="text-[11px] px-2 py-1 rounded-full border border-neon-purple/30 text-neon-purple flex items-center gap-1">
                <button onClick={() => presetAction(() => apiService.applyModelPreset(sp.id), `${sp.name} applied.`)}>{sp.name}</button>
                <button onClick={() => presetAction(() => apiService.deleteModelPreset(sp.id), "Preset deleted.")} className="text-slate-500 hover:text-neon-rose" aria-label="Delete preset">✕</button>
              </span>
            ))}
            <input value={presetName} onChange={(e) => setPresetName(e.target.value)} placeholder="Save current as…"
              className="bg-base-800/80 border border-white/10 rounded-lg px-2 py-0.5 text-[11px] text-slate-200 w-32" />
            <button disabled={!presetName.trim() || busy !== null}
              onClick={() => presetAction(() => apiService.saveModelPreset(presetName.trim()).then(() => setPresetName("")), "Preset saved.")}
              className="text-[11px] text-neon-blue disabled:opacity-40">Save</button>
          </div>
        </section>

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-xs uppercase tracking-widest text-slate-400">Saved models</h4>
            <button onClick={() => { setMessage(null); setChoosing(true); }} className="btn-primary text-xs px-3 py-1.5">
              + Add model
            </button>
          </div>
          {data?.profiles.map((p) => (
            <div key={p.id} className={`glass-panel px-3 py-2 flex items-center gap-3 ${p.enabled ? "" : "opacity-60"}`}>
              <input type="checkbox" checked={p.enabled} onChange={() => toggleEnabled(p)}
                title="Enabled for multi-model analysis" aria-label={`Enable ${p.name}`} />
              <span className={`w-2 h-2 rounded-full shrink-0 ${status[p.id] === undefined ? "bg-slate-600" : status[p.id] ? "bg-neon-emerald" : "bg-neon-rose"}`}
                title={status[p.id] === undefined ? "Checking…" : status[p.id] ? "Connected" : "Unavailable"} />
              <div className="flex-1 min-w-0">
                <div className="text-sm text-slate-200 truncate">
                  {p.name} <span className="text-slate-500 font-mono text-xs">· {p.model}</span>
                  <span className={`ml-2 text-[10px] px-1.5 rounded ${p.is_local ? "bg-neon-emerald/10 text-neon-emerald" : "bg-neon-blue/10 text-neon-blue"}`}>
                    {p.is_local ? "local" : "cloud"}</span>
                  {!p.is_local && <span className="ml-2">{creditBadge(p)}</span>}
                </div>
                <div className="text-[11px] text-slate-500">
                  {p.kind === "anthropic" ? "Anthropic SDK" : p.base_url} · key {p.key_storage === "none" ? "none" : p.api_key} ·
                  {" "}priority {p.priority} ·
                  {" "}{p.daily_limit ? `${p.usage_count}/${p.daily_limit} requests today` : "no daily cap"}
                  {p.hourly_limit ? ` · ${usage[p.id]?.requests_this_hour ?? 0}/${p.hourly_limit} this hour` : ""}
                  {p.allow_practice ? " · practice allowed" : ""}
                </div>
                {usage[p.id] && usage[p.id].requests > 0 && (
                  <div className="text-[10px] text-slate-600">
                    Last 24 h: {usage[p.id].requests} calls ({usage[p.id].failures} failed),{" "}
                    {(usage[p.id].input_tokens + usage[p.id].output_tokens).toLocaleString("en-IN")} tokens
                    {usage[p.id].estimated_cost_usd !== null && ` · ≈ $${usage[p.id].estimated_cost_usd!.toFixed(3)}`}
                  </div>
                )}
                {spendLine(p)}
              </div>
              <button onClick={() => startEdit(p)} className="text-xs text-neon-blue hover:underline">Edit</button>
              <button onClick={() => remove(p)} className="text-xs text-slate-500 hover:text-neon-rose">Delete</button>
            </div>
          ))}
        </section>

        {choosing && (
          <div className="fixed inset-0 z-[60] bg-black/60 backdrop-blur-sm flex items-center justify-center p-6"
            onClick={() => setChoosing(false)}>
            <section role="dialog" aria-label="Add a model" onClick={(e) => e.stopPropagation()}
              className="glass-panel bg-base-900/95 max-w-xl w-full p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h4 className="text-base font-semibold">Add a model</h4>
                <button onClick={() => setChoosing(false)} className="text-slate-500 hover:text-slate-200" aria-label="Close">✕</button>
              </div>
              {(["Local (free, runs on this PC)", "Cloud (paid API)"] as const).map((group, gi) => (
                <div key={group} className="space-y-2">
                  <div className="text-[11px] uppercase tracking-widest text-slate-500">{group}</div>
                  <div className="grid grid-cols-2 gap-2">
                    {presets.filter((p) => p.name.startsWith("Local — ") === (gi === 0)).map((p) => (
                      <button key={p.key} onClick={() => startNew(p)} title={p.notes}
                        className="text-left text-sm px-3 py-2 rounded-lg border border-white/10 text-slate-200 hover:border-neon-blue/40 hover:text-neon-blue">
                        {p.name.replace(/^Local — /, "")}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </section>
          </div>
        )}

        {draft && (
          <div className="fixed inset-0 z-[60] bg-black/60 backdrop-blur-sm flex items-center justify-center p-6"
            onClick={() => busy === null && setDraft(null)}>
          <section role="dialog" aria-label={draft.id ? `Edit ${draft.name}` : "New model"} onClick={(e) => e.stopPropagation()}
            className="glass-panel bg-base-900/95 max-w-3xl w-full max-h-[90vh] overflow-y-auto p-5 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-base font-semibold">{draft.id ? `Edit ${draft.name}` : `New model — ${draft.name}`}</h4>
              <button onClick={() => setDraft(null)} disabled={busy !== null} className="text-slate-500 hover:text-slate-200" aria-label="Close">✕</button>
            </div>
            {draft.notes && <p className="text-[11px] text-slate-400">{draft.notes}</p>}
            <div className="grid md:grid-cols-2 gap-3">
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Name<InfoTip>Your own label for this model, shown in the dropdowns and in each analysis. Change it freely — the record of a model's accuracy follows the model id, not this name.</InfoTip></span>
                <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={input} /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Provider type<InfoTip>How AiTrading talks to the provider. Anthropic is for Claude. Almost everything else — local models (Bionic, LM Studio, Ollama), Kimi, OpenAI, OpenRouter, Gemini — speaks the OpenAI-compatible format.</InfoTip></span>
                <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as ProviderKind })} className={input}>
                  <option value="openai_compatible">OpenAI-compatible (local, Kimi, OpenAI, OpenRouter, Gemini…)</option>
                  <option value="anthropic">Anthropic (Claude)</option>
                </select></label>
              {draft.kind === "openai_compatible" && (
                <label className="text-xs text-slate-400 space-y-1 md:col-span-2"><span className="block">Base URL<InfoTip>The provider's API address, usually ending in /v1. Local models use http://localhost:&lt;port&gt;/v1; cloud providers list theirs in their API docs.</InfoTip></span>
                  <input value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} className={input} placeholder="https://…/v1" /></label>
              )}
              <label className="text-xs text-slate-400 space-y-1 md:col-span-2"><span className="block">
                API key {draft.has_key ? "(leave empty to keep the saved key)" : ""} — or <code>env:VARIABLE_NAME</code> to read it from an environment variable
                <InfoTip>Stored in Windows Credential Manager, never in the database, and never shown again in full. Local models don't need a real key.</InfoTip></span>
                <input type="password" autoComplete="off" value={draft.api_key} onChange={(e) => setDraft({ ...draft, api_key: e.target.value })} className={input}
                  placeholder={draft.kind === "anthropic" ? "sk-ant-…  (empty = use ANTHROPIC_API_KEY)" : "sk-…"} /></label>
              <label className="text-xs text-slate-400 space-y-1 md:col-span-2"><span className="block">Model<InfoTip>The exact model id sent to the provider. "Fetch models" asks the provider which models your key can use and offers them as suggestions.</InfoTip></span>
                <div className="flex gap-2">
                  <input list="model-options" value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} className={input} placeholder="Fetch models, or type the model id" />
                  <datalist id="model-options">{models.map((m) => <option key={m} value={m} />)}</datalist>
                  <button type="button" onClick={fetchModels} disabled={busy !== null} className="btn-secondary text-xs shrink-0 disabled:opacity-50">
                    {busy === "models" ? "Fetching…" : "Fetch models"}
                  </button>
                </div></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Daily request cap (0 = no cap)<InfoTip>The most calls this model may make per day — chat, news reads and stock analyses all count. Once reached, the model is skipped until tomorrow. Your main protection against a surprise bill on a paid API.</InfoTip></span>
                <input inputMode="numeric" value={draft.daily_limit} onChange={(e) => setDraft({ ...draft, daily_limit: Number(e.target.value) || 0 })} className={input} />
                <CostEstimate cap={draft.daily_limit} stats={draft.id ? costStats[draft.id] : undefined}
                  local={/localhost|127\.0\.0\.1/.test(draft.base_url || "") && draft.kind !== "anthropic"}
                  inputPrice={draft.input_price} outputPrice={draft.output_price} /></label>
              <label className="text-xs text-slate-400 flex items-center gap-2 pt-5">
                <input type="checkbox" checked={draft.allow_practice} onChange={(e) => setDraft({ ...draft, allow_practice: e.target.checked })} />
                Allow chart practice (hundreds of calls/day — keep off for paid APIs)
                <InfoTip>Practice replays old charts with the date hidden and grades the model's call at once, to build its track record and the shared lessons quickly. Only runs on the model chosen for Background work, while the market is closed. Free for local models; costly on paid APIs.</InfoTip></label>
              <label className="text-xs text-slate-400 flex items-center gap-2">
                <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
                Enabled for multi-model analysis
                <InfoTip>In Multi-model mode, every enabled model analyses each stock independently and the answers are combined, weighted by evidence and each model's graded accuracy. Untick to keep this model out of those runs (it can still serve chat).</InfoTip></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Priority (lower first)<InfoTip>The order models are run and listed in — a lower number goes first. It does not change how much a model's opinion counts; that comes from its measured accuracy.</InfoTip></span>
                <input inputMode="numeric" value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) || 0 })} className={input} /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Temperature (empty = default)<InfoTip>How much randomness the model uses. The default 0.2 is deliberately low so the same data gives the same call. Raising it makes analyses less consistent.</InfoTip></span>
                <input inputMode="decimal" value={draft.temperature} onChange={(e) => setDraft({ ...draft, temperature: e.target.value })} className={input}
                  placeholder={draft.kind === "anthropic" ? "not used by current Claude models" : "0.2"} /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Max tokens (empty = default)<InfoTip>Longest answer the model may write (default 900, enough for the analysis format). If answers get cut off and show as "not in the required format", raise it to 1500–2000 — especially for models that think before answering, as thinking can count toward this limit.</InfoTip></span>
                <input inputMode="numeric" value={draft.max_tokens} onChange={(e) => setDraft({ ...draft, max_tokens: e.target.value })} className={input} placeholder="900" /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Timeout seconds (empty = default)<InfoTip>How long to wait for one answer before giving up: 120 s for cloud models, 180 s for local ones by default. A call that times out counts as a failed answer for that stock; the other models carry on.</InfoTip></span>
                <input inputMode="numeric" value={draft.timeout_s} onChange={(e) => setDraft({ ...draft, timeout_s: e.target.value })} className={input} placeholder="local 180 / cloud 120" /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Hourly request cap (0 = no cap)<InfoTip>Most calls per hour, checked before each stock analysis. Useful when the provider enforces a per-hour rate limit, or to spread a daily budget across the day.</InfoTip></span>
                <input inputMode="numeric" value={draft.hourly_limit} onChange={(e) => setDraft({ ...draft, hourly_limit: Number(e.target.value) || 0 })} className={input} /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Price per 1M input tokens, USD (for the cost estimate)<InfoTip>From the provider's pricing page. Used only for the "AiTrading spend" estimate (tokens the provider reported × price). Claude prices are known already; for other models, enter them to see a cost instead of just token counts.</InfoTip></span>
                <input inputMode="decimal" value={draft.input_price} onChange={(e) => setDraft({ ...draft, input_price: e.target.value })} className={input} placeholder="e.g. 4" /></label>
              <label className="text-xs text-slate-400 space-y-1"><span className="block">Price per 1M output tokens, USD<InfoTip>Price for the text the model writes back, usually several times the input price. Covers only calls made by AiTrading, not other uses of the same key.</InfoTip></span>
                <input inputMode="decimal" value={draft.output_price} onChange={(e) => setDraft({ ...draft, output_price: e.target.value })} className={input} placeholder="e.g. 20" /></label>
            </div>
            {message && <p className={`text-xs ${message.ok ? "text-neon-emerald" : "text-neon-rose"}`}>{message.text}</p>}
            <div className="flex gap-2 justify-end">
              <button onClick={() => setDraft(null)} className="btn-secondary text-sm">Cancel</button>
              <button onClick={test} disabled={busy !== null} className="btn-secondary text-sm disabled:opacity-50">{busy === "test" ? "Testing…" : "Test"}</button>
              <button onClick={save} disabled={busy !== null} className="btn-primary text-sm disabled:opacity-50">{busy === "save" ? "Saving…" : "Save"}</button>
            </div>
            <p className="text-[10px] text-slate-600">API keys are saved in Windows Credential Manager (not in the database, files or browser) and are never shown again in full. Local runtimes (Bionic, LM Studio, Ollama, vLLM…) only need their URL and model; the key is optional.</p>
          </section>
          </div>
        )}
        {!draft && message && <p className={`text-xs ${message.ok ? "text-neon-emerald" : "text-neon-rose"}`}>{message.text}</p>}

        <section className="flex items-center justify-between glass-panel px-3 py-2">
          <span className="text-xs text-slate-300">Windows notifications for holding alerts (stop-loss, target, loss risk)</span>
          <button onClick={toggleDesktop} className="text-[11px] px-2 py-0.5 rounded-md border border-white/10 text-slate-300 hover:border-neon-blue/40">
            {desktop ? "On — turn off" : "Off — turn on"}
          </button>
        </section>
      </div>
  );
  if (embedded) return content;
  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      {content}
    </div>
  );
}
