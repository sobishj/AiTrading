import type { ChangeEvent } from "react";
import { useSettings } from "../../hooks/useSettings";

// Sentinel string for "never" since <select> values must be strings and
// null can't round-trip through an HTML option value.
const NEVER = "never";

const PRESETS: { value: string; label: string }[] = [
  { value: "60", label: "1 min" },
  { value: "120", label: "2 min" },
  { value: "300", label: "5 min" },
  { value: "600", label: "10 min" },
  { value: "1800", label: "30 min" },
  { value: NEVER, label: "Never" },
];

export default function RefreshIntervalControl() {
  const { settings, loading, saving, setRefreshInterval } = useSettings();

  if (loading || !settings) {
    return <div className="text-xs text-slate-500">Auto-refresh: ...</div>;
  }

  const currentValue = settings.ranking_refresh_seconds === null
    ? NEVER
    : String(settings.ranking_refresh_seconds);

  const handleChange = (e: ChangeEvent<HTMLSelectElement>) => {
    const raw = e.target.value;
    setRefreshInterval(raw === NEVER ? null : Number(raw));
  };

  return (
    <label className="flex items-center gap-2 text-xs text-slate-400">
      <span className="hidden lg:inline">Auto-refresh</span>
      <select
        value={currentValue}
        onChange={handleChange}
        disabled={saving}
        className="bg-base-800/80 border border-white/10 rounded-lg px-2 py-1.5 text-xs
          text-slate-200 focus:outline-none focus:border-neon-blue/50 disabled:opacity-50
          transition-all duration-150"
      >
        {PRESETS.map((preset) => (
          <option key={preset.value} value={preset.value}>
            {preset.label}
          </option>
        ))}
      </select>
    </label>
  );
}
