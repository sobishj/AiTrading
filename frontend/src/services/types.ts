// TypeScript interfaces mirroring backend/schemas.py

export type Action = "BUY" | "WAIT" | "AVOID";
export type RiskLevel = "low" | "medium" | "high";
export type MarketRegime = "risk-on" | "neutral" | "risk-off";
/** auto = the AI-managed universe; manual = the user's own list. */
export type ListMode = "auto" | "manual";

export interface Stock {
  id: number;
  symbol: string;
  name: string;
  sector: string | null;
  current_price: number | null;
  watchlist_status: string;
  last_updated: string;
  rank: number | null;
  conviction_score: number | null;
  previous_rank: number | null;
  action: Action | null;
  strategy: string | null;
  change_pct: number | null;
  change_reason: string | null;
  in_auto_list: boolean;
  in_manual_list: boolean;
  /** Position within the requested list (1 = strongest). */
  list_rank: number | null;
}

export interface TechnicalIndicators {
  stock_id: number;
  rsi: number | null;
  macd: number | null;
  volume: number | null;
  ema_20: number | null;
  ema_50: number | null;
  timestamp: string;
  macd_signal: number | null;
  ema_200: number | null;
  atr: number | null;
  volume_ratio: number | null;
  support: number | null;
  resistance: number | null;
  high_52w: number | null;
  low_52w: number | null;
  return_20d: number | null;
  relative_strength_20d: number | null;
  technical_score: number | null;
  trend: string | null;
  setups: string[];
}

export interface Recommendation {
  id: number;
  stock_id: number;
  symbol: string | null;
  entry_price: number;
  target_price: number;
  stop_loss: number;
  holding_period: string;
  risk_level: string;
  reasoning: string;
  timestamp: string;
  confidence_score: number;
  action: Action | null;
  instrument: string | null;
  strategy: string | null;
  entry_low: number | null;
  entry_high: number | null;
  risk_reward: number | null;
  ai_commentary: string | null;
}

export interface StockDetail extends Stock {
  technical: TechnicalIndicators | null;
  latest_recommendation: Recommendation | null;
}

export interface TradePlan {
  symbol: string;
  name: string;
  rank: number | null;
  action: Action;
  instrument: string;
  strategy: string;
  entry_low: number;
  entry_high: number;
  entry: number;
  target: number;
  stop_loss: number;
  risk_reward: number;
  holding_period: string;
  risk_level: RiskLevel;
  confidence_score: number;
  current_price: number | null;
  change_pct: number | null;
  reasoning: string;
  ai_commentary: string | null;
  invalidation: string;
  exit_logic: string;
  recommendation_id: number | null;
  generated_at: string;
  market_regime: MarketRegime;
  quantity: number;
  capital_required: number;
  risk_amount: number;
  capital: number;
  risk_per_trade_pct: number;
}

export interface Headline {
  title: string;
  link: string | null;
  published: string | null;
  source: string | null;
  sentiment: number;
}

export interface SetupStats {
  strategy: string;
  signals: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_return_pct: number | null;
}

export interface AIForecast {
  date: string;
  direction: "up" | "down" | "flat";
  probability_up: number;
  expected_move_pct: number | null;
  reason: string | null;
  horizon_days: number;
  lessons_version: number;
}

export interface AINewsRead {
  title: string;
  impact: number;
  reason: string | null;
  link: string | null;
}

export interface SectorImpact {
  sector: string;
  impact: number;
  reason: string;
}

export interface MarketOutlook {
  id: number;
  session_date: string;
  bias: "up" | "down" | "flat";
  probability_up: number;
  summary: string | null;
  sector_impacts: SectorImpact[];
  headlines_considered: number;
  generated_at: string;
  actual_return_pct: number | null;
  correct: boolean | null;
}

export interface AIView {
  outlook: {
    session_date: string;
    bias: string;
    probability_up: number;
    summary: string | null;
    sector_impact: SectorImpact | null;
  } | null;
  forecast: AIForecast | null;
  news_reads: AINewsRead[];
  track_record: string;
  trust_weight: number;
  in_shadow_mode: boolean;
  shadow_reason: string | null;
}

export interface AITrackRecord {
  total_forecasts: number;
  graded: number;
  pending: number;
  hit_rate: number | null;
  brier: number | null;
  baseline_hit_rate: number | null;
  edge: number | null;
  by_week: { week: string; forecasts: number; hit_rate: number }[];
  by_lessons_version: { version: number; forecasts: number; hit_rate: number }[];
}

export interface AIPerformance {
  practice: AITrackRecord;
  practice_enabled: boolean;
  practice_today: number;
  practicing_now: boolean;
  outlook: { graded: number; hit_rate: number | null; total: number };
  total_forecasts: number;
  graded: number;
  pending: number;
  hit_rate: number | null;
  brier: number | null;
  baseline_hit_rate: number | null;
  edge: number | null;
  trust_weight: number;
  by_week: { week: string; forecasts: number; hit_rate: number }[];
  by_lessons_version: { version: number; forecasts: number; hit_rate: number }[];
  min_graded_for_trust: number;
  horizon_days: number;
  lessons_version: number;
  lessons: string[];
  enabled: boolean;
  llm_available: boolean;
}

export interface AIPredictionRow {
  id: number;
  symbol: string;
  name: string;
  date: string;
  direction: string;
  probability_up: number;
  expected_move_pct: number | null;
  reason: string | null;
  horizon_days: number;
  price_at_prediction: number;
  lessons_version: number;
  actual_return_pct: number | null;
  correct: boolean | null;
  graded: boolean;
}

export interface StockAnalysis {
  symbol: string;
  name: string;
  rank: number | null;
  action: Action;
  strategy: string;
  conviction_score: number;
  technical_score: number;
  sentiment_score: number;
  summary: string;
  market_context: string;
  technical_confirmation: string[];
  concerns: string[];
  news: Headline[];
  news_sentiment: string;
  risk_factors: string[];
  historical: SetupStats[];
  historical_notes: string[];
  exit_logic: string[];
  score_breakdown: Record<string, number>;
  adjustments: Record<string, number>;
  ai_view: AIView | null;
  generated_at: string;
}

export interface StockNarrative {
  symbol: string;
  narrative: string | null;
  available: boolean;
  generated_at: string;
}

export interface OrderBasket {
  api_key: string | null;
  basket: Record<string, unknown>[];
  kite_url: string;
  publisher_available: boolean;
}

export interface TradeHistoryEntry {
  id: number;
  stock_id: number;
  symbol: string | null;
  execution_date: string;
  entry_price: number;
  exit_price: number | null;
  profit_loss: number | null;
  predicted_target: number | null;
  actual_outcome: string | null;
  timestamp: string;
  quantity: number | null;
  exit_date: string | null;
  source: string | null;
  strategy: string | null;
  recommendation_id: number | null;
}

export interface StrategyPerformance {
  strategy: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_return_pct: number | null;
  real_trades: number;
}

export interface TradeUploadResult {
  rows_processed: number;
  rows_imported: number;
  errors: string[];
  open_positions: number;
  linked_to_recommendations: number;
  duplicates_skipped: number;
  graded: number;
  recalibration: { status: string; sample_size: number } | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  stockContext?: string | null;
}

export interface ChatResponse {
  id: number | null;
  user_message: string;
  ai_response: string;
  stock_context: string | null;
  timestamp: string;
}

export interface IndexQuote {
  ticker: string;
  name: string;
  price: number;
  change_pct: number | null;
}

export interface MarketContext {
  market_date: string;
  fii_activity: number | null;
  dii_activity: number | null;
  news_sentiment: string | null;
  global_market_summary: string | null;
  timestamp: string;
  regime: MarketRegime | null;
  indices: IndexQuote[];
  nifty: { price: number | null; change_pct: number | null; trend: string; return_20d: number | null } | null;
  headlines: string[];
}

export interface MorningBrief {
  brief_date: string;
  generated_at: string;
  market_summary: string | null;
  brief_text: string;
  ai_brief: string | null;
  regime: MarketRegime | null;
  best_symbol: string | null;
  best_plan: TradePlan | null;
  watchlist_top: { symbol: string; name: string; action: Action; strategy: string; conviction: number }[];
  headlines: string[];
}

export interface AppStatus {
  data_source: "yahoo" | "kite";
  kite_connected: boolean;
  llm_available: boolean;
  llm_model: string;
  last_ranked_at: string | null;
  market_regime: MarketRegime;
}

export interface RankingUpdateItem {
  symbol: string;
  rank: number;
  conviction_score: number;
  action: Action;
  strategy: string;
  change_reason: string | null;
}

export interface WsRankingUpdateMessage {
  type: "ranking_update";
  timestamp: string;
  trigger: string | null;
  best_pick: string | null;
  ranking: RankingUpdateItem[];
}

export interface WsMorningBriefMessage extends MorningBrief {
  type: "morning_brief";
}

export interface WsLearningUpdateMessage {
  type: "learning_update";
  graded: number;
}

export interface WsAIOutlookMessage extends MarketOutlook {
  type: "ai_outlook";
}

export interface WsAIUpdateMessage {
  type: "ai_update";
  forecasts?: number;
  lessons_version?: number;
}

export type WsMessage =
  | WsRankingUpdateMessage
  | WsMorningBriefMessage
  | WsLearningUpdateMessage
  | WsAIUpdateMessage
  | WsAIOutlookMessage;

export interface Candle {
  time: string | number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SeriesPoint {
  time: string | number;
  value: number;
}

export interface ChartLevels {
  support?: number | null;
  resistance?: number | null;
  high_52w?: number | null;
  entry_low?: number | null;
  entry_high?: number | null;
  stop_loss?: number | null;
  target?: number | null;
}

export interface ChartData {
  symbol: string;
  interval: string;
  source: string;
  candles: Candle[];
  overlays: {
    ema_20: SeriesPoint[];
    ema_50: SeriesPoint[];
    rsi: SeriesPoint[];
    macd: SeriesPoint[];
    macd_signal: SeriesPoint[];
    macd_hist: SeriesPoint[];
  };
  levels: ChartLevels;
}

export interface AppSettings {
  /** Seconds between background auto-refreshes; null = auto-refresh disabled ("never"). */
  ranking_refresh_seconds: number | null;
  /** Adaptive ranking weights (technical/sentiment/volume), read-only — tuned by the learning cycle. */
  weight_technical: number;
  weight_sentiment: number;
  weight_volume: number;
  capital: number | null;
  risk_per_trade_pct: number | null;
  updated_at: string;
}
