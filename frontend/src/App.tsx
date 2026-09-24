import { useCallback, useEffect, useMemo, useState } from "react";
import AnalysisPanel from "./components/Analysis/AnalysisPanel";
import MorningBriefBar from "./components/BestPick/MorningBriefBar";
import UpdateNotification from "./components/BestPick/UpdateNotification";
import ChatPanel from "./components/Chat/ChatPanel";
import TradingViewChart from "./components/Chart/TradingViewChart";
import LearningModal from "./components/Learning/LearningModal";
import TopBar from "./components/Navigation/TopBar";
import StockList from "./components/StockList/StockList";
import TradePlanCard from "./components/TradePlan/TradePlanCard";
import { useResource } from "./hooks/useResource";
import { useStocks } from "./hooks/useStocks";
import { useTheme } from "./hooks/useTheme";
import { useWebSocket } from "./hooks/useWebSocket";
import MainLayout, { LayoutPreset } from "./layouts/MainLayout";
import apiService from "./services/api";
import type { ListMode, MorningBrief } from "./services/types";

// Fallback polling in case the WebSocket drops; the backend re-ranks on its own cadence.
const STOCK_POLL_MS = 120_000;
const STATUS_POLL_MS = 60_000;
const LIST_MODE_KEY = "tradeai.listMode";

function readListMode(): ListMode {
  try {
    return localStorage.getItem(LIST_MODE_KEY) === "manual" ? "manual" : "auto";
  } catch {
    return "auto";
  }
}

export default function App() {
  const [listMode, setListMode] = useState<ListMode>(readListMode);
  const auto = useStocks("auto", STOCK_POLL_MS);
  const manual = useStocks("manual", STOCK_POLL_MS);
  const current = listMode === "manual" ? manual : auto;
  const stocks = current.stocks;
  const refreshAuto = auto.refresh;
  const refreshManual = manual.refresh;
  const refreshStocks = useCallback(async () => {
    await Promise.all([refreshAuto(), refreshManual()]);
  }, [refreshAuto, refreshManual]);
  // Everything the app knows about, for search suggestions and name lookups.
  const knownStocks = useMemo(() => {
    const bySymbol = new Map(auto.stocks.map((s) => [s.symbol, s]));
    manual.stocks.forEach((s) => bySymbol.set(s.symbol, s));
    return [...bySymbol.values()];
  }, [auto.stocks, manual.stocks]);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [notification, setNotification] = useState<string | null>(null);
  const [liveVersion, setLiveVersion] = useState(0);
  const [statusVersion, setStatusVersion] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [learningOpen, setLearningOpen] = useState(false);
  const [pushedBrief, setPushedBrief] = useState<MorningBrief | null>(null);
  const [presetRequest, setPresetRequest] = useState<{ preset: LayoutPreset; id: number } | null>(null);
  const { theme, toggleTheme } = useTheme();

  const status = useResource(() => apiService.getStatus(), [statusVersion], true);
  const briefResource = useResource(() => apiService.getMorningBrief(), [], true);
  const [outlookVersion, setOutlookVersion] = useState(0);
  const outlookResource = useResource(() => apiService.getAIOutlook(), [outlookVersion], true);
  const brief = pushedBrief ?? briefResource.data;

  useEffect(() => {
    const timer = setInterval(() => setStatusVersion((v) => v + 1), STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, []);

  const { connected } = useWebSocket((message) => {
    if (message.type === "ranking_update") {
      refreshStocks();
      setLiveVersion((v) => v + 1);
      setStatusVersion((v) => v + 1);
      const top = message.ranking.find((r) => r.rank === 1);
      const trigger = message.trigger ?? "";
      if (trigger.startsWith("News on")) {
        setNotification(trigger);
      } else if (top?.change_reason) {
        setNotification(`${top.symbol} is now #1 — ${top.change_reason}`);
      }
    } else if (message.type === "morning_brief") {
      setPushedBrief(message);
      setNotification(
        message.best_plan
          ? `Morning brief: today's best trade is ${message.best_plan.name} (${message.best_plan.strategy}).`
          : "Morning brief: no stock meets the buy criteria today."
      );
    } else if (message.type === "ai_outlook") {
      setOutlookVersion((v) => v + 1);
      setLiveVersion((v) => v + 1);
      setNotification(
        `AI outlook for ${message.session_date}: ${message.bias.toUpperCase()} (${message.probability_up.toFixed(0)}% chance NIFTY closes higher).`
      );
    } else if (message.type === "ai_update") {
      setLiveVersion((v) => v + 1);
      if (message.forecasts) setNotification(`AI analyst made ${message.forecasts} new forecast(s).`);
      if (message.lessons_version) setNotification(`AI analyst reviewed its results and updated its lessons (v${message.lessons_version}).`);
    } else if (message.type === "learning_update" && message.graded > 0) {
      setNotification(`Learning: ${message.graded} recommendation(s) graded against actual prices.`);
    }
  });

  // Open on the strongest opportunity (PRD §4: the app already knows today's best trade).
  useEffect(() => {
    if (!selectedSymbol && stocks.length > 0) {
      const best = brief?.best_symbol;
      setSelectedSymbol(best && stocks.some((s) => s.symbol === best) ? best : stocks[0].symbol);
    }
  }, [stocks, selectedSymbol, brief]);

  const changeListMode = useCallback(
    (mode: ListMode) => {
      setListMode(mode);
      try {
        localStorage.setItem(LIST_MODE_KEY, mode);
      } catch {
        /* storage unavailable */
      }
      const next = mode === "manual" ? manual.stocks : auto.stocks;
      if (!next.some((s) => s.symbol === selectedSymbol)) setSelectedSymbol(next[0]?.symbol ?? null);
    },
    [auto.stocks, manual.stocks, selectedSymbol]
  );

  const selected = useMemo(
    () => knownStocks.find((s) => s.symbol === selectedSymbol) ?? null,
    [knownStocks, selectedSymbol]
  );

  const handleSelect = useCallback((symbol: string) => setSelectedSymbol(symbol), []);
  const dismissNotification = useCallback(() => setNotification(null), []);

  const handleAddToManual = useCallback(
    async (symbol: string) => {
      const stock = await apiService.addToManualList(symbol);
      if (listMode !== "manual") changeListMode("manual");
      setSelectedSymbol(stock.symbol);
      setNotification(`${stock.name} added to your list.`);
      await refreshStocks();
      setLiveVersion((v) => v + 1);
    },
    [refreshStocks, listMode, changeListMode]
  );

  const handleRemoveFromManual = useCallback(
    async (symbol: string) => {
      const name = manual.stocks.find((s) => s.symbol === symbol)?.name ?? symbol;
      await apiService.removeFromManualList(symbol);
      if (symbol === selectedSymbol) {
        const rest = manual.stocks.filter((s) => s.symbol !== symbol);
        setSelectedSymbol(rest[0]?.symbol ?? null);
      }
      setNotification(`${name} removed from your list.`);
      await refreshStocks();
    },
    [manual.stocks, selectedSymbol, refreshStocks]
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const result = await apiService.triggerRefresh();
      await refreshStocks();
      setLiveVersion((v) => v + 1);
      setStatusVersion((v) => v + 1);
      setNotification(
        result.best_pick
          ? `Re-ranked ${result.stocks_evaluated} stocks. Best buy: ${result.best_pick}.`
          : `Re-ranked ${result.stocks_evaluated} stocks. No stock meets the buy criteria right now.`
      );
    } catch (err) {
      setNotification(`Re-rank failed: ${err instanceof Error ? err.message : "unknown error"}`);
    } finally {
      setRefreshing(false);
    }
  }, [refreshStocks]);

  const llmAvailable = status.data?.llm_available ?? false;

  return (
    <>
      <MainLayout
        topBar={
          <TopBar
            stocks={knownStocks}
            status={status.data}
            connected={connected}
            refreshing={refreshing}
            onSelect={handleSelect}
            onAddStock={handleAddToManual}
            onRefresh={handleRefresh}
            onOpenLearning={() => setLearningOpen(true)}
            theme={theme}
            onToggleTheme={toggleTheme}
            onLayoutPreset={(preset) => setPresetRequest({ preset, id: Date.now() })}
          />
        }
        stockList={
          <StockList
            mode={listMode}
            onModeChange={changeListMode}
            stocks={stocks}
            knownStocks={knownStocks}
            loading={current.loading}
            error={current.error}
            selectedSymbol={selectedSymbol}
            onSelect={handleSelect}
            onAdd={handleAddToManual}
            onRemove={handleRemoveFromManual}
          />
        }
        briefBar={
          <MorningBriefBar brief={brief} outlook={outlookResource.data} loading={briefResource.loading} onSelect={handleSelect} />
        }
        chart={<TradingViewChart symbol={selectedSymbol} name={selected?.name} refreshKey={liveVersion} theme={theme} />}
        tradePlan={<TradePlanCard symbol={selectedSymbol} refreshKey={liveVersion} />}
        analysis={<AnalysisPanel symbol={selectedSymbol} refreshKey={liveVersion} llmAvailable={llmAvailable} />}
        presetRequest={presetRequest}
        chat={
          <ChatPanel
            symbol={selectedSymbol}
            name={selected?.name}
            topName={stocks[0]?.name}
            secondName={stocks[1]?.name}
            llmAvailable={llmAvailable}
          />
        }
      />
      <UpdateNotification message={notification} onDismiss={dismissNotification} />
      {learningOpen && (
        <LearningModal
          onClose={() => {
            setLearningOpen(false);
            setLiveVersion((v) => v + 1);
          }}
        />
      )}
    </>
  );
}
