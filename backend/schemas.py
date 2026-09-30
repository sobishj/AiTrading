"""
Pydantic request/response schemas for the AiTrading API.
"""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Stocks
# ---------------------------------------------------------------------------
class StockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    name: str
    sector: Optional[str] = None
    instrument_type: str = "equity"
    current_price: Optional[float] = None
    watchlist_status: str
    last_updated: datetime
    rank: Optional[int] = None
    conviction_score: Optional[float] = None
    # v2: live-ranking extras (the left panel shows names only; these drive
    # subtle rank-change cues and the chat/analysis context)
    previous_rank: Optional[int] = None
    action: Optional[str] = None
    strategy: Optional[str] = None
    change_pct: Optional[float] = None
    change_reason: Optional[str] = None
    in_auto_list: bool = False
    in_manual_list: bool = False
    # Position within the list that was requested (1 = strongest in that list).
    list_rank: Optional[int] = None


class TechnicalIndicatorsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stock_id: int
    rsi: Optional[float] = None
    macd: Optional[float] = None
    volume: Optional[int] = None
    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    timestamp: datetime
    # v2
    macd_signal: Optional[float] = None
    ema_200: Optional[float] = None
    atr: Optional[float] = None
    volume_ratio: Optional[float] = None
    support: Optional[float] = None
    resistance: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    return_20d: Optional[float] = None
    relative_strength_20d: Optional[float] = None
    technical_score: Optional[float] = None
    trend: Optional[str] = None
    setups: list[str] = []


class StockDetailResponse(StockResponse):
    technical: Optional[TechnicalIndicatorsResponse] = None
    latest_recommendation: Optional["RecommendationResponse"] = None


class AddStockRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    name: Optional[str] = Field(default=None, max_length=100)
    sector: Optional[str] = Field(default=None, max_length=50)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
class SetupStatsResponse(BaseModel):
    strategy: str
    signals: int
    wins: int
    losses: int
    win_rate: Optional[float] = None
    avg_return_pct: Optional[float] = None


class HeadlineResponse(BaseModel):
    title: str
    link: Optional[str] = None
    published: Optional[str] = None
    source: Optional[str] = None
    sentiment: int = 0


class StockAnalysisResponse(BaseModel):
    """PRD §9: why the AI ranked this stock — deterministic sections, always available."""
    symbol: str
    name: str
    rank: Optional[int] = None
    action: str
    strategy: str
    conviction_score: float
    technical_score: float
    sentiment_score: float
    summary: str
    market_context: str
    technical_confirmation: list[str]
    concerns: list[str]
    news: list[HeadlineResponse]
    news_sentiment: str
    risk_factors: list[str]
    historical: list[SetupStatsResponse]
    historical_notes: list[str]
    exit_logic: list[str]
    score_breakdown: dict[str, float]
    adjustments: dict[str, float]
    ai_view: Optional[dict] = None
    generated_at: datetime
    # Kept for backward compatibility with the v1 client.
    analysis: str = ""


class StockNarrativeResponse(BaseModel):
    symbol: str
    narrative: Optional[str] = None
    available: bool
    generated_at: datetime


# ---------------------------------------------------------------------------
# Recommendations / trade plans
# ---------------------------------------------------------------------------
class RecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    symbol: Optional[str] = None
    entry_price: float
    target_price: float
    stop_loss: float
    holding_period: str
    risk_level: str
    reasoning: str
    timestamp: datetime
    confidence_score: float
    action: Optional[str] = None
    instrument: Optional[str] = None
    strategy: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    risk_reward: Optional[float] = None
    ai_commentary: Optional[str] = None


class TradePlanResponse(BaseModel):
    """PRD §8 Trade Plan panel for any stock (actionable or not)."""
    symbol: str
    name: str
    rank: Optional[int] = None
    action: str
    instrument: str
    strategy: str
    entry_low: float
    entry_high: float
    entry: float
    target: float
    stop_loss: float
    risk_reward: float
    holding_period: str
    risk_level: str
    confidence_score: float
    current_price: Optional[float] = None
    change_pct: Optional[float] = None
    reasoning: str
    ai_commentary: Optional[str] = None
    invalidation: str
    exit_logic: str
    recommendation_id: Optional[int] = None
    generated_at: datetime
    first_recommended_at: Optional[datetime] = None   # when this (still valid) trade was first recommended
    market_regime: str
    quantity: int
    capital_required: float
    risk_amount: float
    capital: float
    risk_per_trade_pct: float


class OrderBasketResponse(BaseModel):
    """Kite Publisher basket payload: the user reviews and confirms the order on Zerodha."""
    api_key: Optional[str] = None
    basket: list[dict]
    kite_url: str
    publisher_available: bool


# ---------------------------------------------------------------------------
# Trade history / learning
# ---------------------------------------------------------------------------
class TradeHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    symbol: Optional[str] = None
    execution_date: date
    entry_price: float
    exit_price: Optional[float] = None
    profit_loss: Optional[float] = None
    predicted_target: Optional[float] = None
    actual_outcome: Optional[str] = None
    timestamp: datetime
    quantity: Optional[float] = None
    exit_date: Optional[date] = None
    source: Optional[str] = None
    strategy: Optional[str] = None
    recommendation_id: Optional[int] = None


class StrategyPerformanceResponse(BaseModel):
    strategy: str
    trades: int
    wins: int
    losses: int
    win_rate: Optional[float] = None
    avg_return_pct: Optional[float] = None
    real_trades: int = 0


class TradeUploadResponse(BaseModel):
    rows_processed: int
    rows_imported: int
    errors: list[str] = []
    open_positions: int = 0
    linked_to_recommendations: int = 0
    duplicates_skipped: int = 0
    graded: int = 0
    recalibration: Optional[dict] = None


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    stock_context: Optional[str] = None


class ChatMessageResponse(BaseModel):
    user_message: str
    ai_response: str
    stock_context: Optional[str] = None
    timestamp: datetime
    id: Optional[int] = None
    trade_proposal: Optional[dict] = None   # a trade you reported in chat, awaiting your confirmation


# ---------------------------------------------------------------------------
# Market context / morning brief
# ---------------------------------------------------------------------------
class MarketContextResponse(BaseModel):
    market_date: date
    fii_activity: Optional[float] = None
    dii_activity: Optional[float] = None
    news_sentiment: Optional[str] = None
    global_market_summary: Optional[str] = None
    timestamp: datetime
    regime: Optional[str] = None
    indices: list[dict] = []
    nifty: Optional[dict] = None
    headlines: list[str] = []


class MorningBriefResponse(BaseModel):
    brief_date: str
    generated_at: str
    market_summary: Optional[str] = None
    brief_text: str
    ai_brief: Optional[str] = None
    regime: Optional[str] = None
    best_symbol: Optional[str] = None
    best_plan: Optional[dict] = None
    watchlist_top: list[dict] = []
    headlines: list[str] = []


# ---------------------------------------------------------------------------
# App settings (refresh cadence, adaptive weights, position sizing)
# ---------------------------------------------------------------------------
class AppSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ranking_refresh_seconds: Optional[int] = Field(
        default=None, description="Seconds between auto-refreshes; null means auto-refresh is disabled (\"never\")."
    )
    weight_technical: float
    weight_sentiment: float
    weight_volume: float
    capital: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    desktop_notifications: Optional[bool] = None
    updated_at: datetime


class RefreshIntervalUpdateRequest(BaseModel):
    seconds: Optional[int] = Field(
        default=..., ge=30, le=86400,
        description="New auto-refresh interval in seconds. Pass null to disable auto-refresh entirely (\"never\").",
    )


class RiskSettingsUpdateRequest(BaseModel):
    capital: float = Field(..., gt=0, le=1e10)
    risk_per_trade_pct: float = Field(..., gt=0, le=10)


class RecalibrationResponse(BaseModel):
    status: str
    sample_size: int
    previous_weights: Optional[dict[str, float]] = None
    new_weights: Optional[dict[str, float]] = None


StockDetailResponse.model_rebuild()
