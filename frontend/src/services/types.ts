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
  first_recommended_at?: string | null;
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
  model?: string | null;
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

export interface TradeProposalAction {
  side: "BUY" | "SELL";
  quantity: number | null;
  price: number;
  trade_date: string;
  price_check: { day_low: number; day_high: number } | null;
}

/** A trade you reported in chat, checked against real prices, waiting for your confirmation. */
export interface TradeProposal {
  symbol: string | null;
  name: string | null;
  held_quantity: number | null;
  actions: TradeProposalAction[];
  warnings: string[];
  needs: string[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  stockContext?: string | null;
  tradeProposal?: TradeProposal | null;
  /** Assistant messages: the model that wrote the reply. */
  answeredBy?: string | null;
}

export interface ChatResponse {
  id: number | null;
  user_message: string;
  ai_response: string;
  stock_context: string | null;
  timestamp: string;
  trade_proposal?: TradeProposal | null;
  answered_by?: string | null;
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

/** Where the morning pick stands right now (from the live ranking). */
export interface LivePick {
  price: number | null;
  change_pct: number | null;
  rank: number | null;
  action: string | null;
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
  /** Runs on this PC (Bionic, LM Studio, Ollama…). */
  local?: boolean;
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
  sold_quantity?: number;
  avg_sell_price?: number | null;
  avg_buy_price_sold?: number | null;
  realized_pct?: number | null;
  last_sold_on?: string | null;
  closed_at?: string | null;
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

/** What the app can truthfully say about a model's credit (GET /llm/credit). */
export interface ModelCredit {
  profile_id: number;
  /** local = free; balance = live prepaid balance; credit_ok / no_credit = seen on the last real call;
   *  unknown = paid, provider has no balance API and no call yet; error = balance API failed. */
  status: "local" | "balance" | "credit_ok" | "no_credit" | "unknown" | "error";
  label: string;
  amount?: number;
  currency?: string;
  message?: string;
  note?: string;
  checked_at?: string | null;
  error?: string;
}

export interface SpendPeriod {
  requests: number;
  input_tokens: number;
  output_tokens: number;
  /** null when the model has no known price (tokens are still counted). */
  usd: number | null;
  inr: number | null;
}

/** What AiTrading itself spent per model (GET /llm/spend); not the provider's account-wide figure. */
export interface ModelSpend {
  profile_id: number;
  is_local: boolean;
  fx: { rate: number; as_of: string } | null;
  price: { input: number; output: number; source: string } | null;
  today: SpendPeriod;
  month: SpendPeriod;
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

/** Per-model cost basis for the daily-cap estimate (GET /llm/cost-stats). */
export interface ModelCostStats {
  profile_id: number;
  is_local: boolean;
  fx: { rate: number; as_of: string } | null;
  price: { input: number; output: number; source: string } | null;
  tokens_per_call: { input: number; output: number; measured_from_calls: number };
  usd_per_call: number | null;
  recent_usd_per_day: number | null;
  recent_calls_per_day: number;
}

export interface DataSourceRow {
  id: number;
  kind: string;
  name: string;
  url: string | null;
  enabled: boolean;
  builtin: boolean;
  notes: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  last_items: number | null;
}

export interface FeedTest {
  ok: boolean;
  error?: string;
  title?: string;
  count?: number;
  items?: { title: string; published?: string | null }[];
}

export interface GeneralSettings {
  discovery_enabled: boolean;
  universe_screen_time: string;
  auto_list_size: number;
  ai_review_shortlist: number;
  min_traded_value_cr: number;
  defaults: Omit<GeneralSettings, "defaults">;
}

export interface BackupEntry {
  name: string;
  size_bytes: number;
  created_at: string | null;
  rows: number;
  kind: "auto" | "manual";
}

export interface BackupReport {
  ok: boolean;
  problems: string[];
  name: string;
  exported_at: string | null;
  rows: number;
  highlights: Record<string, number>;
  excluded: string[];
}

/** One selected share in a daily discovery run (GET /universe/status). */
export interface DiscoveryPick {
  symbol: string;
  name: string;
  sector: string | null;
  action: string;
  data_score: number;
  selection_score: number;
  strategy: string | null;
  ai?: { signal: string; probability_up: number; confidence: number; votes: string | null };
}

/** The latest daily discovery run: how the Auto list was chosen from the whole market. */
export interface DiscoveryStatus {
  available: boolean;
  running: boolean;
  run_at?: string;
  trigger?: string;
  pool_size?: number;
  eligible?: number;
  ai_reviewed?: number;
  selected_count?: number;
  filtered?: Record<string, number>;
  selected?: DiscoveryPick[];
  added?: string[];
  removed?: { symbol: string; reason: string }[];
  top_sectors?: string[];
  /** NSE filings the AI read for the best candidates in this run. */
  filings_read?: number;
  /** Official NSE sources: last successful fetch (or the error) for filings, calendar, ban, deals, bhavcopy. */
  sources?: Record<string, { ok: boolean; at?: string; error?: string; [k: string]: unknown }>;
  note?: string | null;
  seconds?: number | null;
  auto_list_size: number;
  ai_review_shortlist: number;
  pool_now: number;
}

export interface ModelPresets {
  builtin: { key: string; name: string; description: string; enabled: string[] }[];
  saved: { id: number; name: string }[];
  current: { mode: "single" | "multi"; enabled: number[] };
}

export interface TradingStyle {
  updated_at?: string;
  stats: {
    entries: number;
    closed_trades?: number;
    open_positions?: number;
    win_rate?: number | null;
    avg_win_pct?: number | null;
    avg_loss_pct?: number | null;
    payoff_ratio?: number | null;
    expectancy_pct?: number | null;
    avg_hold_days_winners?: number | null;
    avg_hold_days_losers?: number | null;
    typical_position_inr?: number | null;
    worst_trade_pct?: number | null;
    sectors?: [string, number][];
    followed_ai?: { n: number; avg_10d_pct: number | null };
    against_ai?: { n: number; avg_10d_pct: number | null };
    early_exits?: { n: number; of: number };
  };
  style_notes: string[];
  entries: {
    symbol: string; day: string; price: number; quantity: number; factors: string[]; style: string[];
    rsi: number | null; ret_5d_before: number | null; fwd: Record<string, number>; ai_view: string | null;
    ai_alignment: string | null;
  }[];
  exits: { symbol: string; day: string; price: number; quantity: number; pnl_pct: number | null;
    holding_days: number | null; after_10d_pct: number | null }[];
}
