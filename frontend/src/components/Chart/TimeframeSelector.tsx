export type Timeframe = "5m" | "15m" | "1h" | "1d" | "1w";

export interface TimeframeSpec {
  key: Timeframe;
  label: string;
  interval: string;
  days: number;
  intraday: boolean;
  /** Bars per NSE trading day (for the range presets). */
  barsPerDay: number;
  /** Bars shown when this timeframe is first opened. */
  defaultBars: number;
}

export const TIMEFRAMES: TimeframeSpec[] = [
  { key: "5m", label: "5m", interval: "5minute", days: 14, intraday: true, barsPerDay: 75, defaultBars: 150 },
  { key: "15m", label: "15m", interval: "15minute", days: 45, intraday: true, barsPerDay: 25, defaultBars: 125 },
  { key: "1h", label: "1h", interval: "60minute", days: 180, intraday: true, barsPerDay: 7, defaultBars: 140 },
  { key: "1d", label: "D", interval: "day", days: 1100, intraday: false, barsPerDay: 1, defaultBars: 126 },
  { key: "1w", label: "W", interval: "week", days: 3650, intraday: false, barsPerDay: 0.2, defaultBars: 104 },
];

/** Range presets in NSE trading days. */
export const RANGE_PRESETS: { label: string; tradingDays: number }[] = [
  { label: "1D", tradingDays: 1 },
  { label: "5D", tradingDays: 5 },
  { label: "1M", tradingDays: 21 },
  { label: "3M", tradingDays: 63 },
  { label: "6M", tradingDays: 126 },
  { label: "1Y", tradingDays: 252 },
  { label: "3Y", tradingDays: 756 },
  { label: "5Y", tradingDays: 1260 },
];

interface TimeframeSelectorProps {
  active: Timeframe;
  onChange: (tf: Timeframe) => void;
}

export default function TimeframeSelector({ active, onChange }: TimeframeSelectorProps) {
  return (
    <div className="flex items-center gap-0.5 bg-base-900/60 rounded-lg p-0.5 border border-white/5">
      {TIMEFRAMES.map((tf) => (
        <button
          key={tf.key}
          onClick={() => onChange(tf.key)}
          className={`px-2 py-0.5 rounded-md text-xs font-medium transition-all duration-150 ${
            active === tf.key ? "bg-neon-blue/15 text-neon-blue" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          {tf.label}
        </button>
      ))}
    </div>
  );
}
