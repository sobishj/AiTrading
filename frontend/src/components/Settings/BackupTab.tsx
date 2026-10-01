import { useEffect, useRef, useState } from "react";
import apiService from "../../services/api";
import type { BackupEntry, BackupReport } from "../../services/types";
import InfoTip from "../common/InfoTip";

const errorText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  ?? (e instanceof Error ? e.message : "Something went wrong");
const size = (bytes: number) => bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`;
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—");

/**
 * Settings -> Backup & restore. A backup holds everything the app has learned; restoring replaces all
 * data after an automatic safety backup. API keys are never in a backup file.
 */
export default function BackupTab({ onRestored }: { onRestored: () => void }) {
  const [list, setList] = useState<BackupEntry[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [report, setReport] = useState<BackupReport | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => apiService.listBackups().then(setList).catch((e) => setMessage({ ok: false, text: errorText(e) }));
  useEffect(() => { load(); }, []);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    } finally {
      setBusy(null);
    }
  };

  const download = (name: string) => {
    const a = document.createElement("a");
    a.href = apiService.backupDownloadUrl(name);
    a.download = name;
    a.click();
  };

  const create = () => run("create", async () => {
    const entry = await apiService.createBackup();
    await load();
    download(entry.name);
    setMessage({ ok: true, text: `Backup made (${entry.rows.toLocaleString("en-IN")} records, ${size(entry.size_bytes)}) and downloaded. Keep it somewhere safe — it contains your trades and holdings.` });
  });

  const upload = (file: File) => run("inspect", async () => setReport(await apiService.inspectBackupFile(file)));

  const restore = () => {
    if (!report) return;
    if (!window.confirm("Replace ALL current data with this backup? A safety backup of the current data is made first, so this can be undone.")) return;
    run("restore", async () => {
      const r = await apiService.restoreBackup(report.name);
      setReport(null);
      await load();
      onRestored();
      setMessage({ ok: true, text: `Restored ${r.restored_rows.toLocaleString("en-IN")} records. Your previous data was saved as ${r.safety_backup}.`
        + (r.models_needing_keys.length ? ` Enter the API key again for: ${r.models_needing_keys.join(", ")} (AI models tab).` : "") });
    });
  };

  return (
    <div className="space-y-5">
      <section className="glass-panel p-4 space-y-2">
        <h4 className="text-sm font-semibold">
          Back up
          <InfoTip>Everything the app has learned is in the database: graded forecasts, shared lessons, pattern
            statistics, model track records, ranking weights, your trades, holdings and trader profile, the market pool,
            NSE history, data sources and settings. A backup restored on another PC gives it the same abilities.
            Not included: API keys (kept in Windows Credential Manager — enter them again on the new PC), the local model
            files (install Bionic/Qwen there) and the .env file.</InfoTip>
        </h4>
        <p className="text-[11px] text-slate-400">One file with all your data and everything learned — API keys are never included.</p>
        <button onClick={create} disabled={busy !== null} className="btn-primary text-sm disabled:opacity-50">
          {busy === "create" ? "Backing up…" : "Back up now & download"}
        </button>
      </section>

      <section className="glass-panel p-4 space-y-2">
        <h4 className="text-sm font-semibold">Restore from a file</h4>
        <p className="text-[11px] text-slate-400">The file is checked and summarised first; nothing changes until you confirm.</p>
        <input ref={fileRef} type="file" accept=".zip" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        <button onClick={() => fileRef.current?.click()} disabled={busy !== null} className="btn-secondary text-sm disabled:opacity-50">
          {busy === "inspect" ? "Checking file…" : "Choose backup file…"}
        </button>
        {report && (
          <div className="border border-white/10 rounded-lg p-3 space-y-2 text-xs">
            <div className="text-slate-300">Backup from {when(report.exported_at)} — {report.rows.toLocaleString("en-IN")} records</div>
            <div className="text-slate-400">
              {Object.entries(report.highlights).filter(([, n]) => n > 0).map(([k, n]) => `${n.toLocaleString("en-IN")} ${k}`).join(" · ")}
            </div>
            {report.problems.map((p) => <div key={p} className="text-neon-rose">{p}</div>)}
            {report.ok && (
              <div className="flex gap-2 items-center">
                <button onClick={restore} disabled={busy !== null} className="btn-primary text-xs disabled:opacity-50">
                  {busy === "restore" ? "Restoring…" : "Replace current data with this backup"}
                </button>
                <button onClick={() => setReport(null)} className="text-xs text-slate-500 hover:text-slate-300">Cancel</button>
              </div>
            )}
          </div>
        )}
      </section>

      {message && <p className={`text-xs ${message.ok ? "text-neon-emerald" : "text-neon-rose"}`}>{message.text}</p>}

      <section className="space-y-2">
        <h4 className="text-xs uppercase tracking-widest text-slate-400">
          Backups on this PC
          <InfoTip>Kept in the app's backend\backups folder. "Safety" backups are made automatically before every restore.</InfoTip>
        </h4>
        {list.length === 0 && <p className="text-xs text-slate-500">None yet.</p>}
        {list.map((b) => (
          <div key={b.name} className="glass-panel px-3 py-2 flex items-center gap-3 text-xs">
            <div className="flex-1 min-w-0">
              <div className="text-slate-200 truncate">{when(b.created_at)}
                {b.kind === "auto" && <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-slate-400">safety</span>}
              </div>
              <div className="text-[11px] text-slate-500">{b.rows.toLocaleString("en-IN")} records · {size(b.size_bytes)}</div>
            </div>
            <button onClick={() => download(b.name)} className="text-neon-blue hover:underline">Download</button>
            <button disabled={busy !== null} className="text-slate-400 hover:text-slate-200 disabled:opacity-50"
              onClick={() => run("inspect", async () => setReport(await apiService.inspectSavedBackup(b.name)))}>
              Restore…
            </button>
          </div>
        ))}
      </section>
    </div>
  );
}
