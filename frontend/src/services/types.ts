// TypeScript interfaces mirroring backend/schemas.py

export type Action = "BUY" | "WAIT" | "AVOID";
export type RiskLevel = "low" | "medium" | "high";
export type MarketRegime = "risk-on" | "neutral" | "risk-off";
/** auto = the AI-managed universe; manual = the user's own list. */
export type ListMode = "auto" | "manual" | "holdings";

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

export interface ModelRef {
  id: number;
  name: string;
  kind: string;
  model: string;
}

export interface AppStatus {
  data_source: "yahoo" | "kite";
  kite_connected: boolean;
  llm_available: boolean;
  background_llm_available?: boolean;
  llm_model: string;
  models?: { chat: ModelRef | null; background: ModelRef | null };
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
  | WsPositionAlertsMessage
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
  desktop_notifications?: boolean;
  updated_at: string;
}

// ---------------------------------------------------------------- holdings
export interface PositionTransaction {
  id: number;
  side: "BUY" | "SELL";
  quantity: number;
  price: number;
  date: string;
  realized_pnl: number | null;
}

export interface HoldingSuggestion {
  action: "SELL" | "CONSIDER_SELLING" | "HOLD";
  reason: string;
  suggested_stop: number;
  suggested_target: number;
  r_multiple: number | null;
  plan_action: string | null;
}

export interface Position {
  id: number;
  symbol: string;
  name: string;
  status: "open" | "closed";
  quantity: number;
  avg_price: number;
  invested: number;
  opened_on: string;
  stop_loss: number | null;
  target: number | null;
  notes: string | null;
  last_price: number | null;
  unrealized_pnl: number;
  unrealized_pct: number | null;
  realized_pnl: number;
  loss_risk: number | null;
  risk_reasons: string[];
  suggestion: HoldingSuggestion | null;
  last_checked_at: string | null;
  unread_alerts: number;
  transactions: PositionTransaction[];
}

export type AlertKind = "stop_hit" | "near_stop" | "target_hit" | "near_target" | "trail_stop" | "loss_risk";

export interface PositionAlert {
  id: number;
  position_id: number;
  symbol: string;
  name: string;
  kind: AlertKind;
  severity: "info" | "warning" | "critical";
  title: string;
  message: string;
  price: number;
  loss_risk: number | null;
  acknowledged: boolean;
  created_at: string;
  correct: boolean | null;
}

export interface HoldingsLearning {
  alerts: { kind: string; graded: number; precision: number | null }[];
  risk_threshold: number;
  base_risk_threshold: number;
  closed_positions: number;
  closed_win_rate: number | null;
  realized_pnl: number;
}

export interface WsPositionAlertsMessage {
  type: "position_alerts";
  alerts: PositionAlert[];
}

// ---------------------------------------------------------------- AI models
export type ProviderKind = "openai_compatible" | "anthropic";

export interface LLMProfile {
  id: number;
  name: string;
  kind: ProviderKind;
  base_url: string | null;
  api_key: string;
  has_key: boolean;
  model: string;
  daily_limit: number;
  allow_practice: boolean;
  usage_date: string | null;
  usage_count: number;
  // multi-model settings
  enabled: boolean;
  priority: number;
  temperature: number | null;
  max_tokens: number | null;
  timeout_s: number | null;
  hourly_limit: number;
  input_price: number | null;
  output_price: number | null;
  is_local: boolean;
  key_storage: "none" | "credential_manager" | "environment" | "database";
}

export interface LLMPreset {
  key: string;
  name: string;
  kind: ProviderKind;
  base_url: string;
  api_key: string;
  model: string;
  daily_limit: number;
  allow_practice: boolean;
  notes: string;
}

export interface LLMProfilesResponse {
  profiles: LLMProfile[];
  chat_profile_id: number | null;
  background_profile_id: number | null;
  chat_available: boolean;
  background_available: boolean;
}

export interface LLMProfileInput {
  name: string;
  kind: ProviderKind;
  base_url: string | null;
  api_key: string | null;
  model: string;
  daily_limit: number;
  allow_practice: boolean;
  enabled?: boolean;
  priority?: number;
  temperature?: number | null;
  max_tokens?: number | null;
  timeout_s?: number | null;
  hourly_limit?: number;
  input_price?: number | null;
  output_price?: number | null;
}

// ---------------------------------------------------------------------------
// Multi-model analysis (evidence-based consensus)
// ---------------------------------------------------------------------------
export type ClaimStatus = "SUPPORTED" | "NOT_SUPPORTED" | "UNKNOWN" | "UNVERIFIABLE";

export interface ClaimCheck {
  key: string;
  label: string;
  bias: "bullish" | "bearish" | "risk" | "neutral";
  status: ClaimStatus;
  category: string;
  actual: string;
  model_evidence: string;
  model_confidence: number | null;
  news: { exists: boolean; ref: string; title: string; source: string; published: string; relevance: string } | null;
  flag: string | null;
}

export interface EvidenceReport {
  checks: ClaimCheck[];
  evidence_score: number | null;
  supported: number;
  not_supported: number;
  unknown: number;
  unverifiable: number;
  fabricated_news: number;
  flags: string[];
}

export interface ConsensusMember {
  profile_id: number | null;
  name: string | null;
  model: string;
  recommendation: string | null;
  direction: string;
  stated_confidence: number | null;
  probability_up: number;
  expected_move_pct: number | null;
  entry: number | null;
  target: number | null;
  stop_loss: number | null;
  timeframe: string | null;
  technical: string | null;
  news: string | null;
  risks: string[];
  reasoning: string | null;
  evidence: EvidenceReport | null;
  level_issues: string[];
  weight: number | null;
  reliability_why: string | null;
  evidence_why: string | null;
  latency_ms: number | null;
  fallback: boolean | null;
  tokens: { input: number; output: number };
  outcome: { return_pct: number; correct: boolean } | null;
}

export interface EvidenceFact {
  key: string;
  label: string;
  actual: string;
  bias: string;
  cited_by: string[];
}

export interface HistoricalFactor {
  pattern: string;
  bias: string;
  occurrences: number;
  success_rate: number;
  base_rate: number;
  edge: number;
  source: string;
}

export interface Consensus {
  id?: number;
  available: boolean;
  symbol: string | null;
  created_at?: string;
  trigger?: string;
  signal?: "BUY" | "HOLD" | "SELL";
  probability_up?: number;
  evidence_confidence?: number;
  votes?: Record<string, number>;
  vote_text?: string;
  scores?: { model_probability: number; history_probability: number | null; evidence_support: number; agreement: number };
  levels?: { source: string | null; entry: number | null; target: number | null; stop_loss: number | null } | null;
  evidence_for?: EvidenceFact[];
  risks?: EvidenceFact[];
  historical?: HistoricalFactor[];
  reasoning?: string[];
  disagreement?: { model: string; recommendation: string; reasoning: string; supported: string[]; not_supported: string[] }[];
  members?: ConsensusMember[];
  failed?: { model: string; name: string; error: string }[];
  market_regime?: string | null;
  data_timestamp?: string | null;
  collected_at?: string | null;
  data_source?: string | null;
  disclaimer?: string;
}

export interface ModelPerformance {
  profile_id: number | null;
  model: string;
  graded: number;
  hit_rate: number | null;
  brier: number;
  stated_confidence_avg: number | null;
  confidence_when_right: number | null;
  confidence_when_wrong: number | null;
  evidence_score_avg: number | null;
  avg_pnl_pct: number | null;
  target_hit_rate: number | null;
  stop_hit_rate: number | null;
  dissent: { n: number; hit_rate: number | null };
  by_regime: { label: string; n: number; hit_rate: number | null }[];
  by_setup: { label: string; n: number; hit_rate: number | null }[];
}

export interface PatternRow {
  pattern: string;
  bias: string;
  regime: string;
  source: "history" | "live";
  occurrences: number;
  success_rate: number;
  base_rate: number | null;
  edge: number | null;
  avg_return_pct: number;
}

export interface ModelUsage {
  profile_id: number;
  name: string;
  model: string;
  is_local: boolean;
  requests: number;
  failures: number;
  requests_this_hour: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number | null;
  daily_limit: number;
  hourly_limit: number;
}

export interface ModelPresets {
  builtin: { key: string; name: string; description: string; enabled: string[] }[];
  saved: { id: number; name: string }[];
  current: { mode: "single" | "multi"; enabled: number[] };
}
