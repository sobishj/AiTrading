import {
  ColorType,
  CrosshairMode,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineStyle,
  MouseEventParams,
  Time,
  UTCTimestamp,
  createChart,
} from "lightweight-charts";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Theme } from "../../hooks/useTheme";
import type { ChartData, SeriesPoint } from "../../services/types";
import type { DrawingTool } from "./DrawingTools";

export interface IndicatorToggles {
  ema: boolean;
  volume: boolean;
  rsi: boolean;
  macd: boolean;
  levels: boolean;
}

export type Drawing =
  | { kind: "horizontal"; price: number }
  | { kind: "trendline"; points: [{ time: Time; value: number }, { time: Time; value: number }] };

/** Imperative navigation used by the toolbar under the chart. */
export interface ChartNavigation {
  /** factor < 1 zooms in, > 1 zooms out (anchored at the latest bar). */
  zoom: (factor: number) => void;
  /** Scroll by a fraction of the visible width; negative = back in time. */
  pan: (fraction: number) => void;
  /** Show the most recent `bars` bars. */
  showLast: (bars: number) => void;
  fit: () => void;
  barCount: () => number;
}

interface CandlestickChartProps {
  data: ChartData | null;
  /** Changes when the symbol or timeframe changes; the default view is applied only then, not on live refreshes. */
  viewKey: string;
  /** Bars shown by default for a new symbol/timeframe. */
  defaultBars: number;
  intraday: boolean;
  show: IndicatorToggles;
  drawingTool: DrawingTool;
  drawings: Drawing[];
  onAddDrawing: (drawing: Drawing) => void;
  theme: Theme;
}

const IST_OFFSET_SECONDS = 19800;
interface Palette {
  up: string;
  down: string;
  ema20: string;
  ema50: string;
  rsi: string;
  macd: string;
  signal: string;
  drawing: string;
  text: string;
  grid: string;
  border: string;
  /** r,g,b triplets for translucent fills */
  upRgb: string;
  downRgb: string;
  blueRgb: string;
}

const PALETTES: Record<Theme, Palette> = {
  dark: {
    up: "#22d3a5", down: "#ff5c7a", ema20: "#3ba7ff", ema50: "#9b6bff", rsi: "#f5b94a",
    macd: "#3ba7ff", signal: "#ff9f5a", drawing: "#e2e8f0", text: "#94a3b8",
    grid: "rgba(255,255,255,0.035)", border: "rgba(255,255,255,0.08)",
    upRgb: "34,211,165", downRgb: "255,92,122", blueRgb: "59,167,255",
  },
  light: {
    up: "#0a9f74", down: "#dc2f55", ema20: "#1668d6", ema50: "#7c4ddf", rsi: "#c77d06",
    macd: "#1668d6", signal: "#e0701f", drawing: "#1e293b", text: "#475569",
    grid: "rgba(15,23,42,0.06)", border: "rgba(15,23,42,0.15)",
    upRgb: "10,159,116", downRgb: "220,47,85", blueRgb: "22,104,214",
  },
};

/** Daily/weekly bars as business days; intraday as seconds shifted so the (UTC) axis reads IST. */
function toChartTime(time: string | number, intraday: boolean): Time {
  if (typeof time === "number") return time as UTCTimestamp;
  if (!intraday) return time.slice(0, 10) as Time;
  return (Math.floor(new Date(time).getTime() / 1000) + IST_OFFSET_SECONDS) as UTCTimestamp;
}

function timeKey(time: Time): number {
  if (typeof time === "number") return time;
  if (typeof time === "string") return Date.parse(time) / 1000;
  return Date.UTC(time.year, time.month - 1, time.day) / 1000;
}

/** Map + de-duplicate (keep the latest value per timestamp) so series times strictly increase. */
function toSeries<T extends { time: string | number }>(rows: T[], intraday: boolean, map: (row: T) => object) {
  const byTime = new Map<string | number, object & { time: Time }>();
  for (const row of rows) {
    const time = toChartTime(row.time, intraday);
    byTime.set(typeof time === "object" ? JSON.stringify(time) : time, { time, ...map(row) });
  }
  return [...byTime.values()].sort((a, b) => timeKey(a.time) - timeKey(b.time));
}

function lineData(points: SeriesPoint[], intraday: boolean) {
  return toSeries(points, intraday, (p) => ({ value: p.value }));
}

interface Legend {
  open: number;
  high: number;
  low: number;
  close: number;
  change: number;
}

const MIN_VISIBLE_BARS = 10;

const CandlestickChart = forwardRef<ChartNavigation, CandlestickChartProps>(function CandlestickChart(
  { data, viewKey, defaultBars, intraday, show, drawingTool, drawings, onAddDrawing, theme },
  navRef
) {
  const COLORS = PALETTES[theme];
  const barCountRef = useRef(0);
  const appliedViewKey = useRef<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const series = useRef<{
    candle?: ISeriesApi<"Candlestick">;
    volume?: ISeriesApi<"Histogram">;
    ema20?: ISeriesApi<"Line">;
    ema50?: ISeriesApi<"Line">;
    rsi?: ISeriesApi<"Line">;
    macd?: ISeriesApi<"Line">;
    signal?: ISeriesApi<"Line">;
    hist?: ISeriesApi<"Histogram">;
  }>({});
  const levelLines = useRef<IPriceLine[]>([]);
  const drawingLines = useRef<IPriceLine[]>([]);
  const trendSeries = useRef<ISeriesApi<"Line">[]>([]);
  const pendingPoint = useRef<{ time: Time; value: number } | null>(null);
  const toolRef = useRef(drawingTool);
  const onAddRef = useRef(onAddDrawing);
  const [legend, setLegend] = useState<Legend | null>(null);
  const [pending, setPending] = useState(false);

  toolRef.current = drawingTool;
  onAddRef.current = onAddDrawing;

  function setRange(from: number, to: number) {
    const chart = chartRef.current;
    if (!chart) return;
    const last = Math.max(0, barCountRef.current - 1);
    const width = to - from;
    // Keep at least a sliver of data on screen.
    if (to > last + width * 0.5) {
      to = last + width * 0.5;
      from = to - width;
    }
    if (from < -width * 0.5) {
      from = -width * 0.5;
      to = from + width;
    }
    chart.timeScale().setVisibleLogicalRange({ from, to });
  }

  function panBars(bars: number) {
    const range = chartRef.current?.timeScale().getVisibleLogicalRange();
    if (range) setRange(range.from + bars, range.to + bars);
  }

  function zoomRange(factor: number, anchorFraction: number) {
    const range = chartRef.current?.timeScale().getVisibleLogicalRange();
    if (!range) return;
    const width = range.to - range.from;
    const newWidth = Math.min(Math.max(width * factor, MIN_VISIBLE_BARS), barCountRef.current + 20);
    const anchor = range.from + width * anchorFraction;
    const from = anchor - (anchor - range.from) * (newWidth / width);
    setRange(from, from + newWidth);
  }

  function showLast(bars: number) {
    const count = barCountRef.current;
    if (!chartRef.current || count === 0) return;
    if (bars >= count) {
      chartRef.current.timeScale().fitContent();
      return;
    }
    chartRef.current.timeScale().setVisibleLogicalRange({ from: count - bars - 0.5, to: count - 1 + 3 });
  }

  useImperativeHandle(navRef, () => ({
    zoom: (factor: number) => zoomRange(factor, 1),
    pan: (fraction: number) => {
      const range = chartRef.current?.timeScale().getVisibleLogicalRange();
      if (range) panBars((range.to - range.from) * fraction);
    },
    showLast,
    fit: () => chartRef.current?.timeScale().fitContent(),
    barCount: () => barCountRef.current,
  }));

  // Create the chart and all series (again whenever the theme changes).
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: COLORS.text,
        fontSize: 11,
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      rightPriceScale: { borderColor: COLORS.border },
      timeScale: { borderColor: COLORS.border, rightOffset: 6 },
      crosshair: { mode: CrosshairMode.Normal },
      // The mouse wheel is handled below (scroll through time; Ctrl+wheel zooms).
      // By default the library turns a normal wheel into zoom, which makes it
      // hard to move back in time with a mouse.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      kineticScroll: { mouse: true, touch: true },
      width: container.clientWidth,
      height: container.clientHeight,
    });

    const s = series.current;
    s.candle = chart.addCandlestickSeries({
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderVisible: false,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });
    s.volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "volume", lastValueVisible: false, priceLineVisible: false });
    s.ema20 = chart.addLineSeries({ color: COLORS.ema20, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    s.ema50 = chart.addLineSeries({ color: COLORS.ema50, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    s.rsi = chart.addLineSeries({ color: COLORS.rsi, lineWidth: 1, priceScaleId: "rsi", priceLineVisible: false, lastValueVisible: true });
    s.rsi.createPriceLine({ price: 70, color: `rgba(${COLORS.downRgb},0.35)`, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    s.rsi.createPriceLine({ price: 30, color: `rgba(${COLORS.upRgb},0.35)`, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    s.hist = chart.addHistogramSeries({ priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false });
    s.macd = chart.addLineSeries({ color: COLORS.macd, lineWidth: 1, priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false });
    s.signal = chart.addLineSeries({ color: COLORS.signal, lineWidth: 1, priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false });

    chartRef.current = chart;

    const observer = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    });
    observer.observe(container);
    appliedViewKey.current = null; // a new chart instance needs its default view again

    const onWheel = (e: WheelEvent) => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range) return;
      e.preventDefault();
      const width = range.to - range.from;
      if (e.ctrlKey || e.metaKey) {
        const rect = container.getBoundingClientRect();
        const anchor = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
        zoomRange(e.deltaY > 0 ? 1.15 : 1 / 1.15, anchor);
        return;
      }
      // Vertical or horizontal wheel both scroll through time: down/right = back in time.
      const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? -e.deltaX : e.deltaY;
      const strength = Math.min(3, Math.max(0.5, Math.abs(delta) / 100));
      panBars(-Math.sign(delta) * Math.max(1, width * 0.08) * strength);
    };
    container.addEventListener("wheel", onWheel, { passive: false });

    const onMove = (param: MouseEventParams) => {
      const bar = s.candle ? (param.seriesData.get(s.candle) as { open: number; high: number; low: number; close: number } | undefined) : undefined;
      if (!bar || bar.open === undefined) {
        setLegend(null);
        return;
      }
      setLegend({ ...bar, change: ((bar.close - bar.open) / bar.open) * 100 });
    };
    chart.subscribeCrosshairMove(onMove);

    const onClick = (param: MouseEventParams) => {
      const tool = toolRef.current;
      if (!param.point || !s.candle || tool === "none") return;
      const price = s.candle.coordinateToPrice(param.point.y);
      if (price === null) return;
      if (tool === "horizontal") {
        onAddRef.current({ kind: "horizontal", price: Number(price.toFixed(2)) });
        return;
      }
      if (tool === "trendline" && param.time !== undefined) {
        const point = { time: param.time, value: Number(price.toFixed(2)) };
        if (!pendingPoint.current) {
          pendingPoint.current = point;
          setPending(true);
          return;
        }
        const first = pendingPoint.current;
        pendingPoint.current = null;
        setPending(false);
        if (timeKey(first.time) === timeKey(point.time)) return;
        const ordered = [first, point].sort((a, b) => timeKey(a.time) - timeKey(b.time)) as [typeof first, typeof point];
        onAddRef.current({ kind: "trendline", points: ordered });
      }
    };
    chart.subscribeClick(onClick);

    return () => {
      observer.disconnect();
      container.removeEventListener("wheel", onWheel);
      chart.unsubscribeCrosshairMove(onMove);
      chart.unsubscribeClick(onClick);
      chart.remove();
      chartRef.current = null;
      series.current = {};
      levelLines.current = [];
      drawingLines.current = [];
      trendSeries.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  // Pane layout: price on top, then volume, RSI and MACD bands at the bottom.
  useEffect(() => {
    const chart = chartRef.current;
    const s = series.current;
    if (!chart) return;
    const bands = [show.rsi, show.macd].filter(Boolean).length;
    const bandHeight = 0.16;
    const priceBottom = 0.06 + bands * bandHeight + (show.volume ? 0.1 : 0);
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: priceBottom } });
    const volumeTop = 1 - bands * bandHeight - 0.14;
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: volumeTop, bottom: bands * bandHeight + 0.02 } });
    let bandIndex = 0;
    if (show.rsi) {
      const bottom = (bands - 1 - bandIndex) * bandHeight;
      chart.priceScale("rsi").applyOptions({ scaleMargins: { top: 1 - bottom - bandHeight + 0.02, bottom } });
      bandIndex += 1;
    }
    if (show.macd) {
      const bottom = (bands - 1 - bandIndex) * bandHeight;
      chart.priceScale("macd").applyOptions({ scaleMargins: { top: 1 - bottom - bandHeight + 0.02, bottom } });
    }
    s.volume?.applyOptions({ visible: show.volume });
    s.ema20?.applyOptions({ visible: show.ema });
    s.ema50?.applyOptions({ visible: show.ema });
    s.rsi?.applyOptions({ visible: show.rsi });
    s.macd?.applyOptions({ visible: show.macd });
    s.signal?.applyOptions({ visible: show.macd });
    s.hist?.applyOptions({ visible: show.macd });
  }, [show, theme]);

  // Data.
  useEffect(() => {
    const s = series.current;
    if (!s.candle || !data) return;
    s.candle.setData(toSeries(data.candles, intraday, (c) => ({ open: c.open, high: c.high, low: c.low, close: c.close })) as never);
    s.volume?.setData(
      toSeries(data.candles, intraday, (c) => ({
        value: c.volume,
        color: c.close >= c.open ? `rgba(${COLORS.upRgb},0.35)` : `rgba(${COLORS.downRgb},0.35)`,
      })) as never
    );
    s.ema20?.setData(lineData(data.overlays.ema_20, intraday) as never);
    s.ema50?.setData(lineData(data.overlays.ema_50, intraday) as never);
    s.rsi?.setData(lineData(data.overlays.rsi, intraday) as never);
    s.macd?.setData(lineData(data.overlays.macd, intraday) as never);
    s.signal?.setData(lineData(data.overlays.macd_signal, intraday) as never);
    s.hist?.setData(
      toSeries(data.overlays.macd_hist, intraday, (p) => ({
        value: p.value,
        color: p.value >= 0 ? `rgba(${COLORS.upRgb},0.45)` : `rgba(${COLORS.downRgb},0.45)`,
      })) as never
    );
    barCountRef.current = data.candles.length;
    // Default view only for a new symbol/timeframe (or a re-created chart);
    // live refreshes keep whatever the user has scrolled or zoomed to.
    if (appliedViewKey.current !== viewKey) {
      appliedViewKey.current = viewKey;
      showLast(defaultBars);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, intraday, theme, viewKey]);

  // Support/resistance and trade-plan levels.
  useEffect(() => {
    const candle = series.current.candle;
    if (!candle) return;
    levelLines.current.forEach((line) => candle.removePriceLine(line));
    levelLines.current = [];
    if (!data || !show.levels) return;
    const l = data.levels;
    const add = (price: number | null | undefined, color: string, title: string, style = LineStyle.Dashed) => {
      if (price) levelLines.current.push(candle.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, title, axisLabelVisible: true }));
    };
    add(l.support, `rgba(${COLORS.upRgb},0.5)`, "Support", LineStyle.Dotted);
    add(l.resistance, `rgba(${COLORS.downRgb},0.5)`, "Resistance", LineStyle.Dotted);
    add(l.entry_high, `rgba(${COLORS.blueRgb},0.85)`, "Entry");
    add(l.entry_low, `rgba(${COLORS.blueRgb},0.55)`, "");
    add(l.stop_loss, `rgba(${COLORS.downRgb},0.9)`, "Stop", LineStyle.Solid);
    add(l.target, `rgba(${COLORS.upRgb},0.9)`, "Target", LineStyle.Solid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, show.levels, theme]);

  // User drawings.
  useEffect(() => {
    const chart = chartRef.current;
    const candle = series.current.candle;
    if (!chart || !candle) return;
    drawingLines.current.forEach((line) => candle.removePriceLine(line));
    trendSeries.current.forEach((line) => chart.removeSeries(line));
    drawingLines.current = [];
    trendSeries.current = [];
    for (const d of drawings) {
      if (d.kind === "horizontal") {
        drawingLines.current.push(
          candle.createPriceLine({ price: d.price, color: COLORS.drawing, lineWidth: 1, lineStyle: LineStyle.Solid, title: "", axisLabelVisible: true })
        );
      } else {
        const line = chart.addLineSeries({ color: COLORS.drawing, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        line.setData(d.points as never);
        trendSeries.current.push(line);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawings, theme]);

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className={`absolute inset-0 ${drawingTool !== "none" ? "cursor-crosshair" : ""}`} />
      <div className="absolute top-1 left-2 z-10 pointer-events-none text-[11px] font-mono flex gap-3 text-slate-400">
        {legend && (
          <>
            <span>O <span className="text-slate-200">{legend.open.toFixed(2)}</span></span>
            <span>H <span className="text-slate-200">{legend.high.toFixed(2)}</span></span>
            <span>L <span className="text-slate-200">{legend.low.toFixed(2)}</span></span>
            <span>C <span className="text-slate-200">{legend.close.toFixed(2)}</span></span>
            <span className={legend.change >= 0 ? "text-neon-emerald" : "text-neon-rose"}>
              {legend.change >= 0 ? "+" : ""}
              {legend.change.toFixed(2)}%
            </span>
          </>
        )}
        {show.ema && !legend && (
          <>
            <span style={{ color: COLORS.ema20 }}>EMA 20</span>
            <span style={{ color: COLORS.ema50 }}>EMA 50</span>
          </>
        )}
        {pending && <span className="text-neon-purple">Click the second point…</span>}
      </div>
    </div>
  );
});

export default CandlestickChart;
