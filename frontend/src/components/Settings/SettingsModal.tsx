import { useState } from "react";
import BackupTab from "./BackupTab";
import GeneralTab from "./GeneralTab";
import ModelSettingsModal from "./ModelSettingsModal";
import SourcesTab from "./SourcesTab";

export type SettingsTab = "general" | "sources" | "backup" | "models";

const TABS: { key: SettingsTab; label: string }[] = [
  { key: "models", label: "AI models" },
  { key: "sources", label: "Data sources" },
  { key: "general", label: "General" },
  { key: "backup", label: "Backup & restore" },
];

interface SettingsModalProps {
  initialTab?: SettingsTab;
  onClose: () => void;
  /** Something that affects the top bar / lists changed (models, sources, a restore). */
  onChanged: () => void;
}

/** One window for every setting: AI models, data sources, general options, backup & restore. */
export default function SettingsModal({ initialTab = "models", onClose, onChanged }: SettingsModalProps) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      <div className="glass-panel bg-base-900/95 max-w-3xl w-full h-[88vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 pt-5">
          <h3 className="text-lg font-semibold">Settings</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Close">✕</button>
        </div>
        <div className="flex gap-1 px-6 pt-3 border-b border-white/5" role="tablist">
          {TABS.map((t) => (
            <button key={t.key} role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
              className={`px-3 py-1.5 text-xs font-medium rounded-t-md border-b-2 -mb-px transition-colors ${
                tab === t.key ? "border-neon-blue text-neon-blue" : "border-transparent text-slate-400 hover:text-slate-200"}`}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-6">
          {tab === "models" && <ModelSettingsModal embedded onClose={onClose} onChanged={onChanged} />}
          {tab === "sources" && <SourcesTab />}
          {tab === "general" && <GeneralTab />}
          {tab === "backup" && <BackupTab onRestored={onChanged} />}
        </div>
      </div>
    </div>
  );
}
