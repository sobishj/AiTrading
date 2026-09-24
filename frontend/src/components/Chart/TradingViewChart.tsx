import { useCallback, useEffect, useRef, useState } from "react";
import { useResource } from "../../hooks/useResource";
import type { Theme } from "../../hooks/useTheme";
import apiService from "../../services/api";
import CandlestickChart, { ChartNavigation, Drawing, IndicatorToggles } from "./CandlestickChart";
import DrawingTools, { DrawingTool } from "./DrawingTools";
import TimeframeSelector, { RANGE_PRESETS, TIMEFRAMES, Timeframe } from "./TimeframeSelector";

interface TradingViewChartProps {
  symbol: string | null;
  name?: string;
  /** Bumped by the parent on live ranking updates so levels and the last bar refresh. */
  refreshKey: number;
  theme: Theme;
}

const DEFAULT_TOGGLES: IndicatorToggles = { ema: true, volume: true, rsi: true, macd: true, levels: true };
const TOGGLE_LABELS: { key: keyof IndicatorToggles; label: string }[] = [
  { key: "ema", label: "EMA" },
  { key: "volume", label: "Vol" },
  { key: "rsi", label: "RSI" },
  { key: "macd", label: "MACD" },
  { key: "levels", label: "Levels" },
];

function storageKey(symbol: string, timeframe: Timeframe) {
  return `tradeai.drawings.${symbol}.${timeframe}`;
}

function loadDrawings(symbol: string, timeframe: Timeframe): Drawing[] {
  try {
    return JSON.parse(localStorage.getItem(storageKey(symbol, timeframe)) ?? "[]") as Drawing[];
  } catch {
    return [];
  }
}

function saveDrawings(symbol: string, timeframe: Timeframe, drawings: Drawing[]) {
  try {
    localStorage.setItem(storageKey(symbol, timeframe), JSON.stringify(drawings));
  } catch {
    /* storage unavailable: drawings stay for this session only */
  }
}

/** PRD §7 center chart: candles, timeframes, drawing tools, S/R and trade-plan levels, EMA/RSI/MACD/volume. */
export default function TradingViewChart({ symbol, name, refreshKey, theme }: TradingViewChartProps) {
  const [timeframe, setTimeframe] = useState<Timeframe>("1d");
  const [drawingTool, setDrawingTool] = useState<DrawingTool>("none");
  const [toggles, setToggles] = useState<IndicatorToggles>(DEFAULT_TOGGLES);
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const navRef = useRef<ChartNavigation>(null);
  const tf = TIMEFRAMES.find((t) => t.key === timeframe) ?? TIMEFRAMES[3];

  const { data, loading, error } = useResource(
    symbol ? () => apiService.getChart(symbol, tf.interval, tf.days) : null,
    [symbol, timeframe, refreshKey],
    true
  );
  // Don't show the previous symbol's candles while the new symbol loads.
  const chartData = data && data.symbol === symbol && data.interval === tf.interval ? data : null;

  useEffect(() => {
    setDrawings(symbol ? loadDrawings(symbol, timeframe) : []);
  }, [symbol, timeframe]);

  const addDrawing = useCallback(
    (drawing: Drawing) => {
      if (!symbol) return;
      setDrawings((prev) => {
        const next = [...prev, drawing];
        saveDrawings(symbol, timeframe, next);
        return next;
      });
    },
    [symbol, timeframe]
  );

  const clearDrawings = useCallback(() => {
    if (!symbol) return;
    saveDrawings(symbol, timeframe, []);
    setDrawings([]);
  }, [symbol, timeframe]);

  if (!symbol) {
    return (
      <div className="glass-panel h-full flex items-center justify-center text-slate-500 text-sm">
        Select a stock to view its chart
      </div>
    );
  }

  const last = chartData?.candles[chartData.candles.length - 1];
  const prev = chartData?.candles[chartData.candles.length - 2];
  const change = last && prev ? ((last.close - prev.close) / prev.close) * 100 : null;

  return (
    <div className="glass-panel h-full flex flex-col overflow-hidden">
      <div className="px-4 py-2 border-b border-white/5 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-baseline gap-2 min-w-0">
          <span className="font-semibold text-sm truncate">{name ?? symbol}</span>
          <span className="text-[11px] text-slate-500 font-mono">NSE:{symbol}</span>
          {last && (
            <span className="text-sm font-mono text-slate-200">
              ₹{last.close.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
            </span>
          )}
          {change !== null && (
            <span className={`text-xs font-mono ${change >= 0 ? "text-neon-emerald" : "text-neon-rose"}`}>
              {change >= 0 ? "+" : ""}
              {change.toFixed(2)}%
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex items-center gap-0.5">
            {TOGGLE_LABELS.map((t) => (
              <button
                key={t.key}
                onClick={() => setToggles((prev) => ({ ...prev, [t.key]: !prev[t.key] }))}
                className={`px-1.5 py-0.5 rounded text-[11px] font-medium transition-colors ${
                  toggles[t.key] ? "text-neon-blue bg-neon-blue/10" : "text-slate-500 hover:text-slate-300"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <DrawingTools
            active={drawingTool}
            onChange={setDrawingTool}
            onClear={clearDrawings}
            hasDrawings={drawings.length > 0}
          />
          <TimeframeSelector active={timeframe} onChange={setTimeframe} />
        </div>
      </div>

      <div className="flex-1 min-h-[120px] px-1 py-1 relative overflow-hidden">
        {loading && !chartData && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-500 z-10">
            Loading chart…
          </div>
        )}
        {!loading && (error || (chartData && chartData.candles.length === 0)) && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-500 z-10 text-center px-6">
            {error ? `Chart unavailable: ${error}` : "No candle data for this timeframe."}
          </div>
        )}
        <CandlestickChart
          ref={navRef}
          data={chartData}
          viewKey={`${symbol}|${timeframe}`}
          defaultBars={tf.defaultBars}
          intraday={tf.intraday}
          show={toggles}
          drawingTool={drawingTool}
          drawings={drawings}
          onAddDrawing={addDrawing}
          theme={theme}
        />
      </div>
      {chartData && chartData.candles.length > 0 && (
        <div className="px-3 py-1 border-t border-white/5 flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-0.5">
            {RANGE_PRESETS.filter((r) => {
              const bars = Math.round(r.tradingDays * tf.barsPerDay);
              return bars >= 10 && bars <= chartData.candles.length * 1.2;
            }).map((r) => (
              <button
                key={r.label}
                onClick={() => navRef.current?.showLast(Math.round(r.tradingDays * tf.barsPerDay))}
                className="px-1.5 py-0.5 rounded text-[11px] font-medium text-slate-400 hover:text-neon-blue hover:bg-neon-blue/10"
                title={`Show the last ${r.label}`}
              >
                {r.label}
              </button>
            ))}
            <button
              onClick={() => navRef.current?.fit()}
              className="px-1.5 py-0.5 rounded text-[11px] font-medium text-slate-400 hover:text-neon-blue hover:bg-neon-blue/10"
              title="Show all loaded history"
            >
              All
            </button>
          </div>
          <div className="flex items-center gap-1">
            <span
              className="hidden xl:inline text-[10px] text-slate-600 mr-1"
              title={`Data: ${chartData.source === "kite" ? "Zerodha Kite" : "Yahoo Finance (may be ~15 min delayed)"}`}
            >
              Wheel: scroll · Ctrl+wheel: zoom · Drag: pan
            </span>
            {[
              { label: "◀", title: "Scroll back in time", act: () => navRef.current?.pan(-0.3) },
              { label: "▶", title: "Scroll forward in time", act: () => navRef.current?.pan(0.3) },
              { label: "−", title: "Zoom out", act: () => navRef.current?.zoom(1.4) },
              { label: "+", title: "Zoom in", act: () => navRef.current?.zoom(1 / 1.4) },
            ].map((b) => (
              <button
                key={b.title}
                onClick={b.act}
                title={b.title}
                aria-label={b.title}
                className="w-6 h-6 rounded-md text-xs text-slate-300 bg-white/5 hover:bg-neon-blue/15 hover:text-neon-blue"
              >
                {b.label}
              </button>
            ))}
            <button
              onClick={() => navRef.current?.showLast(tf.defaultBars)}
              title="Reset to the default view"
              className="px-2 h-6 rounded-md text-[11px] text-slate-300 bg-white/5 hover:bg-neon-blue/15 hover:text-neon-blue"
            >
              Reset
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
