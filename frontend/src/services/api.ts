import axios, { AxiosInstance } from "axios";
import type {
  AIPerformance,
  MarketOutlook,
  AIPredictionRow,
  AppSettings,
  AppStatus,
  Candle,
  ListMode,
  ChartData,
  ChatResponse,
  MarketContext,
  MorningBrief,
  OrderBasket,
  Recommendation,
  Stock,
  StockAnalysis,
  StockDetail,
  StockNarrative,
  StrategyPerformance,
  TradeHistoryEntry,
  TradePlan,
  TradeUploadResult,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

class ApiService {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: `${API_BASE_URL}/api`,
      // Rankings, plans and analysis are deterministic and return in a few
      // seconds. Only chat and the analysis narrative wait on the local LLM,
      // which can take 15-60s on CPU; those calls pass a longer timeout.
      timeout: 30000,
      headers: { "Content-Type": "application/json" },
    });

    this.client.interceptors.response.use(
      (response) => response,
      (error) => {
        const detail = error.response?.data?.detail;
        const message =
          (typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : null) ??
          error.message ??
          "Unknown API error";
        console.error(`[API] ${error.config?.url ?? "request"} failed: ${message}`);
        return Promise.reject(new Error(message));
      }
    );
  }

  private static enc(symbol: string): string {
    return encodeURIComponent(symbol);
  }

  async getStatus(): Promise<AppStatus> {
    const { data } = await this.client.get<AppStatus>("/status");
    return data;
  }

  async getStocks(list: ListMode | "all" = "auto"): Promise<Stock[]> {
    const { data } = await this.client.get<Stock[]>("/stocks", { params: { list } });
    return data;
  }

  /** Add a share to the user's Manual list; it's ranked before this resolves. */
  async addToManualList(symbol: string): Promise<Stock> {
    const { data } = await this.client.post<Stock>("/watchlist/manual", { symbol }, { timeout: 60000 });
    return data;
  }

  async removeFromManualList(symbol: string): Promise<void> {
    await this.client.delete(`/watchlist/manual/${ApiService.enc(symbol)}`);
  }

  async addStock(symbol: string): Promise<Stock> {
    const { data } = await this.client.post<Stock>("/stocks", { symbol });
    return data;
  }

  async removeStock(symbol: string): Promise<void> {
    await this.client.delete(`/stocks/${ApiService.enc(symbol)}`);
  }

  async getStockDetail(symbol: string): Promise<StockDetail> {
    const { data } = await this.client.get<StockDetail>(`/stocks/${ApiService.enc(symbol)}`);
    return data;
  }

  async getStockCandles(symbol: string, interval = "day", days = 180): Promise<Candle[]> {
    const { data } = await this.client.get<Candle[]>(`/stocks/${ApiService.enc(symbol)}/candles`, {
      params: { interval, days },
    });
    return data;
  }

  async getChart(symbol: string, interval = "day", days = 365): Promise<ChartData> {
    const { data } = await this.client.get<ChartData>(`/stocks/${ApiService.enc(symbol)}/chart`, {
      params: { interval, days },
    });
    return data;
  }

  async getTradePlan(symbol: string): Promise<TradePlan> {
    const { data } = await this.client.get<TradePlan>(`/stocks/${ApiService.enc(symbol)}/trade-plan`);
    return data;
  }

  async getAnalysis(symbol: string): Promise<StockAnalysis> {
    const { data } = await this.client.get<StockAnalysis>(`/stocks/${ApiService.enc(symbol)}/analysis`);
    return data;
  }

  async getNarrative(symbol: string): Promise<StockNarrative> {
    const { data } = await this.client.get<StockNarrative>(
      `/stocks/${ApiService.enc(symbol)}/analysis/narrative`,
      { timeout: 180000 }
    );
    return data;
  }

  async getOrderBasket(symbol: string): Promise<OrderBasket> {
    const { data } = await this.client.get<OrderBasket>(`/stocks/${ApiService.enc(symbol)}/order-basket`);
    return data;
  }

  async getRecommendations(): Promise<Recommendation | null> {
    const { data } = await this.client.get<Recommendation | null>("/recommendations");
    return data;
  }

  async getMorningBrief(): Promise<MorningBrief> {
    const { data } = await this.client.get<MorningBrief>("/morning-brief", { timeout: 60000 });
    return data;
  }

  async sendChatMessage(message: string, stockContext?: string | null): Promise<ChatResponse> {
    const { data } = await this.client.post<ChatResponse>(
      "/chat",
      { message, stock_context: stockContext ?? null },
      { timeout: 180000 }
    );
    return data;
  }

  async getChatHistory(limit = 50): Promise<ChatResponse[]> {
    const { data } = await this.client.get<ChatResponse[]>("/chat/history", { params: { limit } });
    return data;
  }

  async clearChatHistory(): Promise<void> {
    await this.client.delete("/chat/history");
  }

  async getTradeHistory(limit = 50, source?: string): Promise<TradeHistoryEntry[]> {
    const { data } = await this.client.get<TradeHistoryEntry[]>("/trade-history", {
      params: { limit, ...(source ? { source } : {}) },
    });
    return data;
  }

  async getStrategies(): Promise<StrategyPerformance[]> {
    const { data } = await this.client.get<StrategyPerformance[]>("/strategies");
    return data;
  }

  async getMarketContext(): Promise<MarketContext> {
    const { data } = await this.client.get<MarketContext>("/market-context");
    return data;
  }

  async uploadTrades(file: File): Promise<TradeUploadResult> {
    const formData = new FormData();
    formData.append("file", file);
    const { data } = await this.client.post<TradeUploadResult>("/upload-trades", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 120000,
    });
    return data;
  }

  async triggerRefresh(): Promise<{ status: string; stocks_evaluated: number; best_pick: string | null }> {
    const { data } = await this.client.post("/refresh", undefined, { timeout: 60000 });
    return data;
  }

  async getAIPerformance(): Promise<AIPerformance> {
    const { data } = await this.client.get<AIPerformance>("/ai/performance");
    return data;
  }

  async getAIPredictions(limit = 30, symbol?: string): Promise<AIPredictionRow[]> {
    const { data } = await this.client.get<AIPredictionRow[]>("/ai/predictions", {
      params: { limit, ...(symbol ? { symbol } : {}) },
    });
    return data;
  }

  async getAIOutlook(): Promise<MarketOutlook | null> {
    const { data } = await this.client.get<MarketOutlook | null>("/ai/outlook");
    return data;
  }

  async queueAIOutlook(): Promise<void> {
    await this.client.post("/ai/outlook-now");
  }

  async setAIPractice(enabled: boolean): Promise<void> {
    await this.client.put("/ai/practice", { enabled });
  }

  async queueAIForecasts(): Promise<void> {
    await this.client.post("/ai/forecast-now");
  }

  async queueAIReflection(): Promise<{ graded: number }> {
    const { data } = await this.client.post<{ status: string; graded: number }>("/ai/reflect", undefined, { timeout: 120000 });
    return data;
  }

  aiTrainingDataUrl(): string {
    return `${API_BASE_URL}/api/ai/training-data`;
  }

  async getSettings(): Promise<AppSettings> {
    const { data } = await this.client.get<AppSettings>("/settings");
    return data;
  }

  /** Pass null to disable auto-refresh entirely ("never"). */
  async setRefreshInterval(seconds: number | null): Promise<AppSettings> {
    const { data } = await this.client.put<AppSettings>("/settings/refresh-interval", { seconds });
    return data;
  }

  async setRiskSettings(capital: number, riskPerTradePct: number): Promise<AppSettings> {
    const { data } = await this.client.put<AppSettings>("/settings/risk", {
      capital,
      risk_per_trade_pct: riskPerTradePct,
    });
    return data;
  }
}

export const apiService = new ApiService();
export default apiService;
