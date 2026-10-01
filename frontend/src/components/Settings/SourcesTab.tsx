import { useEffect, useState } from "react";
import apiService from "../../services/api";
import type { DataSourceRow, FeedTest } from "../../services/types";
import InfoTip from "../common/InfoTip";

const errorText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  ?? (e instanceof Error ? e.message : "Something went wrong");

const ago = (iso: string | null) => {
  if (!iso) return null;
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return minutes < 1 ? "just now" : minutes < 60 ? `${minutes} min ago` : minutes < 1440
    ? `${Math.round(minutes / 60)} h ago` : `${Math.round(minutes / 1440)} d ago`;
};

/** Settings -> Data sources: every source the app reads, with status; add, switch off or remove news feeds. */
export default function SourcesTab() {
  const [rows, setRows] = useState<DataSourceRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [test, setTest] = useState<FeedTest | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => apiService.getSources().then((r) => { setRows(r); setError(null); }).catch((e) => setError(errorText(e)));
  useEffect(() => { load(); }, []);

  const run = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const testFeed = () => run("test", async () => setTest(await apiService.testSource(url.trim())));
  const addFeed = () => run("add", async () => {
    await apiService.addSource(url.trim(), name.trim() || undefined);
    setUrl(""); setName(""); setTest(null);
    await load();
  });

  const feeds = (rows ?? []).filter((r) => r.kind === "rss");
  const nse = (rows ?? []).filter((r) => r.kind !== "rss");

  const status = (r: DataSourceRow) => {
    if (!r.enabled) return <span className="text-slate-500">off</span>;
    if (r.last_error && (!r.last_ok_at)) return <span className="text-neon-rose" title={r.last_error}>failing</span>;
    if (r.last_ok_at) {
      return (
        <span className={r.last_error ? "text-amber-300" : "text-neon-emerald"} title={r.last_error ?? undefined}>
          {r.last_error ? "last try failed · " : "✓ "}{ago(r.last_ok_at)}{r.last_items != null ? ` · ${r.last_items} items` : ""}
        </span>
      );
    }
    return <span className="text-slate-500">not fetched yet</span>;
  };

  const usefulness = (r: DataSourceRow) => {
    const u = r.usefulness;
    if (!u || (r.kind !== "rss" && r.kind !== "nse_filings")) return null;
    if (!u.judged) {
      return <div className="text-[11px] text-slate-500">Usefulness: {u.n} of 30 judged reads so far</div>;
    }
    const weak = (u.rate ?? 0) < 52;
    return (
      <div className={`text-[11px] ${weak ? "text-amber-300" : "text-slate-400"}`}
        title="Of the AI's positive/negative reads of this source's items, how often the price moved that way over the next 5 sessions (NSE closing prices). 50% is a coin flip.">
        Usefulness: the AI's reads were right {u.rate}% of {u.n} times
        {weak && " — adds little; consider removing it"}
      </div>
    );
  };

  const row = (r: DataSourceRow) => (
    <div key={r.id} className={`glass-panel px-3 py-2 flex items-center gap-3 ${r.enabled ? "" : "opacity-60"}`}>
      <input type="checkbox" checked={r.enabled} disabled={busy !== null} aria-label={`Use ${r.name}`}
        onChange={() => run(`t${r.id}`, async () => { await apiService.updateSource(r.id, { enabled: !r.enabled }); await load(); })} />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-slate-200 truncate">
          {r.name}
          {r.builtin && <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-slate-400">built-in</span>}
        </div>
        <div className="text-[11px] text-slate-500 truncate">{r.url ?? r.notes}</div>
        <div className="text-[11px]">{status(r)}</div>
        {usefulness(r)}
      </div>
      {!r.builtin && (
        <button disabled={busy !== null} className="text-xs text-slate-500 hover:text-neon-rose"
          onClick={() => { if (window.confirm(`Remove "${r.name}"?`)) run(`d${r.id}`, async () => { await apiService.deleteSource(r.id); await load(); }); }}>
          Remove
        </button>
      )}
    </div>
  );

  return (
    <div className="space-y-5">
      {error && <p className="text-xs text-neon-rose">{error}</p>}

      <section className="space-y-2">
        <h4 className="text-xs uppercase tracking-widest text-slate-400">
          News feeds
          <InfoTip>Headlines from these feeds are matched to shares by name and read by the AI. Any site's RSS or Atom
            feed works — look for an "RSS" link on the site. Feeds are checked before they're added. Ordinary web pages
            aren't accepted: they have no stable format, often forbid automated reading, and hidden text in a page could
            mislead an AI that reads it.</InfoTip>
        </h4>
        {rows === null && !error && <p className="text-xs text-slate-500">Loading…</p>}
        {feeds.map(row)}
        <div className="glass-panel p-3 space-y-2">
          <div className="text-xs text-slate-300">Add a news feed</div>
          <div className="flex gap-2">
            <input value={url} onChange={(e) => { setUrl(e.target.value); setTest(null); }} placeholder="https://…/rss"
              className="flex-1 bg-base-800/80 border border-white/10 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-neon-blue/50" />
            <button onClick={testFeed} disabled={!url.trim() || busy !== null} className="btn-secondary text-xs disabled:opacity-50">
              {busy === "test" ? "Checking…" : "Check"}
            </button>
          </div>
          {test && !test.ok && <p className="text-[11px] text-neon-rose">{test.error}</p>}
          {test?.ok && (
            <div className="space-y-2">
              <p className="text-[11px] text-neon-emerald">Works — "{test.title}", {test.count} items. Latest:</p>
              <ul className="text-[11px] text-slate-400 list-disc pl-5 space-y-0.5">
                {test.items?.map((i, n) => <li key={n}>{i.title}</li>)}
              </ul>
              <div className="flex gap-2">
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder={`Name (default: ${test.title})`}
                  className="flex-1 bg-base-800/80 border border-white/10 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-neon-blue/50" />
                <button onClick={addFeed} disabled={busy !== null} className="btn-primary text-xs disabled:opacity-50">
                  {busy === "add" ? "Adding…" : "Add feed"}
                </button>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="space-y-2">
        <h4 className="text-xs uppercase tracking-widest text-slate-400">
          NSE official data
          <InfoTip>Free, official exchange data. Switch one off and the app stops using it straight away (its effect on
            scores is removed); switch it on and it's fetched again immediately. Built-in sources can't be deleted.</InfoTip>
        </h4>
        {nse.map(row)}
      </section>

      <section className="space-y-1 text-[11px] text-slate-500">
        <div>Prices: Zerodha Kite when connected, otherwise Yahoo Finance — gaps are filled and checked against NSE's
          official close.</div>
        <button onClick={() => run("defaults", async () => { setRows(await apiService.restoreDefaultSources()); })}
          disabled={busy !== null} className="text-neon-blue hover:underline disabled:opacity-50">
          Switch all built-in sources back on
        </button>
      </section>
    </div>
  );
}
