import axios, { AxiosInstance } from "axios";
import type {
  HoldingsLearning,
  Consensus,
  LLMPreset,
  ModelPerformance,
  ModelPresets,
  ModelUsage,
  PatternRow,
  TradingStyle,
  LLMProfile,
  ModelCredit,
  ModelSpend,
  DiscoveryStatus,
  ModelCostStats,
  DataSourceRow,
  FeedTest,
  GeneralSettings,
  BackupEntry,
  BackupReport,
  LLMProfileInput,
  LLMProfilesResponse,
  Position,
  PositionAlert,
  ProviderKind,
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

  // ---------------------------------------------------------------- holdings
  async recordTrades(symbol: string, actions: { side: "BUY" | "SELL"; quantity: number; price: number; trade_date: string }[]) {
    const { data } = await this.client.post("/trades/record", { symbol, actions });
    return data as { symbol: string; recorded: { side: string; quantity: number; price: number; trade_date: string }[] };
  }

  async getTradingStyle() {
    const { data } = await this.client.get("/profile/trading-style");
    return data as TradingStyle;
  }

  async getPositions(status: "open" | "closed" | "all" = "open"): Promise<Position[]> {
    const { data } = await this.client.get<Position[]>("/positions", { params: { status } });
    return data;
  }

  async getPositionForSymbol(symbol: string): Promise<Position | null> {
    const { data } = await this.client.get<Position | null>(`/positions/by-symbol/${ApiService.enc(symbol)}`);
    return data;
  }

  async buyShares(input: {
    symbol: string; quantity: number; price?: number | null; trade_date?: string | null;
    stop_loss?: number | null; target?: number | null; notes?: string | null;
  }): Promise<Position> {
    const { data } = await this.client.post<Position>("/positions", input, { timeout: 60000 });
    return data;
  }

  async sellShares(positionId: number, input: { quantity: number; price: number; trade_date?: string | null }): Promise<Position> {
    const { data } = await this.client.post<Position>(`/positions/${positionId}/sell`, input);
    return data;
  }

  async updatePosition(positionId: number, input: { stop_loss?: number | null; target?: number | null; notes?: string | null }): Promise<Position> {
    const { data } = await this.client.patch<Position>(`/positions/${positionId}`, input);
    return data;
  }

  async getPosition(positionId: number): Promise<Position> {
    const { data } = await this.client.get<Position>(`/positions/${positionId}`);
    return data;
  }

  async editTransaction(positionId: number, txId: number, input: { quantity: number; price: number; trade_date: string }): Promise<Position> {
    const { data } = await this.client.patch<Position>(`/positions/${positionId}/transactions/${txId}`, input, { timeout: 60000 });
    return data;
  }

  /** Returns null when deleting the last buy removed the whole holding. */
  async deleteTransaction(positionId: number, txId: number): Promise<Position | null> {
    const { data } = await this.client.delete<Position | null>(`/positions/${positionId}/transactions/${txId}`, { timeout: 60000 });
    return data;
  }

  async deletePosition(positionId: number): Promise<void> {
    await this.client.delete(`/positions/${positionId}`);
  }

  async getAlerts(limit = 30): Promise<{ unread: number; alerts: PositionAlert[] }> {
    const { data } = await this.client.get<{ unread: number; alerts: PositionAlert[] }>("/alerts", { params: { limit } });
    return data;
  }

  async acknowledgeAlerts(ids?: number[]): Promise<void> {
    await this.client.post("/alerts/ack", { ids: ids ?? null });
  }

  async getHoldingsLearning(): Promise<HoldingsLearning> {
    const { data } = await this.client.get<HoldingsLearning>("/positions/learning");
    return data;
  }

  // ---------------------------------------------------------------- AI models
  // ---- multi-model analysis ----
  async runAIAnalysis(symbol: string, opts: { profile_ids?: number[]; use_all?: boolean; force?: boolean } = {}): Promise<Consensus> {
    // Several models answer in parallel; a local model alone can take a few minutes.
    const { data } = await this.client.post<Consensus>(`/ai/analyze/${encodeURIComponent(symbol)}`, opts, { timeout: 900000 });
    return data;
  }

  async getConsensus(symbol: string): Promise<Consensus> {
    const { data } = await this.client.get<Consensus>(`/ai/consensus/${encodeURIComponent(symbol)}`);
    return data;
  }

  async getModelPerformance(): Promise<ModelPerformance[]> {
    const { data } = await this.client.get<ModelPerformance[]>("/ai/models/performance");
    return data;
  }

  async getPatterns(source?: "history" | "live"): Promise<PatternRow[]> {
    const { data } = await this.client.get<PatternRow[]>("/ai/patterns", { params: source ? { source } : {} });
    return data;
  }

  async getModelUsage(): Promise<ModelUsage[]> {
    const { data } = await this.client.get<ModelUsage[]>("/ai/usage");
    return data;
  }

  async getProviderStatus(): Promise<{ id: number; connected: boolean }[]> {
    const { data } = await this.client.get<{ id: number; connected: boolean }[]>("/llm/status", { timeout: 30000 });
    return data;
  }

  async setAnalysisMode(mode: "single" | "multi"): Promise<void> {
    await this.client.put("/ai/mode", { mode });
  }

  async getModelPresets(): Promise<ModelPresets> {
    const { data } = await this.client.get<ModelPresets>("/ai/presets");
    return data;
  }

  async applyBuiltinPreset(key: string): Promise<void> {
    await this.client.post(`/ai/presets/builtin/${key}/apply`);
  }

  async saveModelPreset(name: string): Promise<void> {
    await this.client.post("/ai/presets", { name });
  }

  async applyModelPreset(id: number): Promise<void> {
    await this.client.post(`/ai/presets/${id}/apply`);
  }

  async deleteModelPreset(id: number): Promise<void> {
    await this.client.delete(`/ai/presets/${id}`);
  }

  async getLLMPresets(): Promise<LLMPreset[]> {
    const { data } = await this.client.get<LLMPreset[]>("/llm/presets");
    return data;
  }

  async getModelCredit(refresh = false): Promise<ModelCredit[]> {
    const { data } = await this.client.get<ModelCredit[]>("/llm/credit", { params: { refresh }, timeout: 30000 });
    return data;
  }

  async getCostStats(): Promise<ModelCostStats[]> {
    const { data } = await this.client.get<ModelCostStats[]>("/llm/cost-stats");
    return data;
  }

  async getSources(): Promise<DataSourceRow[]> {
    const { data } = await this.client.get<DataSourceRow[]>("/sources");
    return data;
  }

  async testSource(url: string): Promise<FeedTest> {
    const { data } = await this.client.post<FeedTest>("/sources/test", { url });
    return data;
  }

  async addSource(url: string, name?: string): Promise<DataSourceRow> {
    const { data } = await this.client.post<DataSourceRow>("/sources", { url, name });
    return data;
  }

  async updateSource(id: number, patch: { enabled?: boolean; name?: string }): Promise<DataSourceRow> {
    const { data } = await this.client.patch<DataSourceRow>(`/sources/${id}`, patch);
    return data;
  }

  async deleteSource(id: number): Promise<void> {
    await this.client.delete(`/sources/${id}`);
  }

  async restoreDefaultSources(): Promise<DataSourceRow[]> {
    const { data } = await this.client.post<DataSourceRow[]>("/sources/restore-defaults");
    return data;
  }

  async getGeneralSettings(): Promise<GeneralSettings> {
    const { data } = await this.client.get<GeneralSettings>("/settings/general");
    return data;
  }

  async saveGeneralSettings(patch: Partial<Omit<GeneralSettings, "defaults">>): Promise<GeneralSettings> {
    const { data } = await this.client.put<GeneralSettings>("/settings/general", patch);
    return data;
  }

  async listBackups(): Promise<BackupEntry[]> {
    const { data } = await this.client.get<BackupEntry[]>("/backup/list");
    return data;
  }

  async createBackup(): Promise<BackupEntry> {
    const { data } = await this.client.post<BackupEntry>("/backup/create", undefined, { timeout: 300000 });
    return data;
  }

  backupDownloadUrl(name: string): string {
    return `${this.client.defaults.baseURL}/backup/download/${encodeURIComponent(name)}`;
  }

  async inspectBackupFile(file: File): Promise<BackupReport> {
    const form = new FormData();
    form.append("file", file);
    const { data } = await this.client.post<BackupReport>("/backup/inspect", form, {
      headers: { "Content-Type": "multipart/form-data" }, timeout: 300000 });
    return data;
  }

  async inspectSavedBackup(name: string): Promise<BackupReport> {
    const { data } = await this.client.post<BackupReport>(`/backup/inspect/${encodeURIComponent(name)}`);
    return data;
  }

  async restoreBackup(name: string): Promise<{ restored_rows: number; safety_backup: string; models_needing_keys: string[] }> {
    const { data } = await this.client.post("/backup/restore", { name }, { timeout: 600000 });
    return data;
  }

  async getDiscoveryStatus(): Promise<DiscoveryStatus> {
    const { data } = await this.client.get<DiscoveryStatus>("/universe/status");
    return data;
  }

  async runDiscovery(): Promise<{ status: string }> {
    const { data } = await this.client.post<{ status: string }>("/universe/run");
    return data;
  }

  async getModelSpend(): Promise<ModelSpend[]> {
    const { data } = await this.client.get<ModelSpend[]>("/llm/spend", { timeout: 30000 });
    return data;
  }

  async checkModelCredit(profileId: number): Promise<ModelCredit> {
    const { data } = await this.client.post<ModelCredit>(`/llm/profiles/${profileId}/check-credit`, undefined, { timeout: 120000 });
    return data;
  }

  async getLLMProfiles(): Promise<LLMProfilesResponse> {
    const { data } = await this.client.get<LLMProfilesResponse>("/llm/profiles", { timeout: 30000 });
    return data;
  }

  async saveLLMProfile(input: LLMProfileInput, id?: number): Promise<LLMProfile> {
    const { data } = id
      ? await this.client.put<LLMProfile>(`/llm/profiles/${id}`, input)
      : await this.client.post<LLMProfile>("/llm/profiles", input);
    return data;
  }

  async deleteLLMProfile(id: number): Promise<void> {
    await this.client.delete(`/llm/profiles/${id}`);
  }

  async setActiveModels(chatProfileId: number, backgroundProfileId: number): Promise<void> {
    await this.client.put("/llm/active", { chat_profile_id: chatProfileId, background_profile_id: backgroundProfileId });
  }

  async listProviderModels(input: { kind: ProviderKind; base_url?: string | null; api_key?: string | null; profile_id?: number | null }): Promise<string[]> {
    const { data } = await this.client.post<{ models: string[] }>("/llm/models", input, { timeout: 30000 });
    return data.models;
  }

  async testProvider(input: { kind: ProviderKind; base_url?: string | null; api_key?: string | null; model: string; profile_id?: number | null }): Promise<{ ok: boolean; reply?: string; error?: string; seconds: number }> {
    const { data } = await this.client.post("/llm/test", input, { timeout: 120000 });
    return data;
  }

  async setDesktopNotifications(desktop: boolean): Promise<void> {
    await this.client.put("/settings/notifications", { desktop });
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
