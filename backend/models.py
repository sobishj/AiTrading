"""
SQLAlchemy ORM models mirroring config/database.sql.
"""
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    UniqueConstraint,
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, or_,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # One of: equity, etf, future, option. Drives the top-bar mode tab filter
    # (Swing/Intraday show equities, ETF/Futures/Options show their own type).
    instrument_type: Mapped[str] = mapped_column(String(20), default="equity")
    current_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    watchlist_status: Mapped[str] = mapped_column(String(20), default="active")
    # Comma-separated news keywords (see universe.py); null falls back to symbol + name.
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The user's own "Manual" list, independent of the AI-managed "Auto" universe
    # (watchlist_status == "active"). A stock can be in either list or both.
    in_manual_list: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # watchlist_status: "active" = in today's Auto list; "candidate" = in the market pool, not selected
    # today; "excluded" = you removed it from Auto, so discovery never re-adds it; "inactive" = other.
    # Last time NSE's NIFTY 500 list included this share (null = never seen there).
    universe_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="stock", cascade="all, delete-orphan")
    trade_history: Mapped[list["TradeHistory"]] = relationship(back_populates="stock", cascade="all, delete-orphan")
    technical_indicators: Mapped[list["TechnicalIndicators"]] = relationship(back_populates="stock", cascade="all, delete-orphan")


def tracked_stock_filter():
    """Stocks the engine ranks and watches: the Auto universe, the Manual list, and open holdings."""
    from sqlalchemy import select  # local: Position is defined further down this module
    held = select(Position.stock_id).where(Position.status == "open")
    return or_(Stock.watchlist_status == "active", Stock.in_manual_list.is_(True), Stock.id.in_(held))


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    entry_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    target_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric, nullable=False)
    holding_period: Mapped[str] = mapped_column(String(50), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    confidence_score: Mapped[float] = mapped_column(Numeric, default=0)

    # Component scores at time of recommendation, persisted so the nightly
    # digest can later correlate them against actual outcomes and adapt the
    # ranking weights (see ranking_service.recalibrate_weights).
    technical_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    volume_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # v2 trade-plan fields. entry_price stays the midpoint of the entry zone so
    # older code paths (grading, chat context) keep working.
    action: Mapped[str | None] = mapped_column(String(10), nullable=True)
    instrument: Mapped[str | None] = mapped_column(String(40), nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    entry_low: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    entry_high: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # Market memory: indicators, news, drivers and regime at the moment of the
    # call, as JSON, so every recommendation stays explainable after the fact.
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional narrative from the local LLM, filled in asynchronously.
    ai_commentary: Mapped[str | None] = mapped_column(Text, nullable=True)

    stock: Mapped["Stock"] = relationship(back_populates="recommendations")
    trade_history: Mapped[list["TradeHistory"]] = relationship(back_populates="recommendation")


class TradeHistory(Base):
    __tablename__ = "trade_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    # Links a graded outcome back to the recommendation that produced it, when
    # known (set by the nightly digest's auto-grading step). Manually uploaded
    # Zerodha trades have no recommendation to link to, so this stays null.
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True, index=True)
    execution_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    profit_loss: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    predicted_target: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    actual_outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    quantity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # "auto" = graded by the learning engine from price action;
    # "zerodha" = a real trade imported from a tradebook upload.
    source: Mapped[str] = mapped_column(String(20), default="auto")
    # Dedupe key for imported trades (Zerodha trade_id(s), or a content hash),
    # so re-uploading the same tradebook doesn't duplicate rows.
    trade_ref: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)

    stock: Mapped["Stock"] = relationship(back_populates="trade_history")
    recommendation: Mapped["Recommendation | None"] = relationship(back_populates="trade_history")


class UserChat(Base):
    __tablename__ = "user_chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    ai_response: Mapped[str] = mapped_column(Text, nullable=False)
    stock_context: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Which saved model answered ("Claude (Anthropic API) · claude-opus-5-5"); null for older rows.
    answered_by: Mapped[str | None] = mapped_column(String(300), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TechnicalIndicators(Base):
    __tablename__ = "technical_indicators"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    rsi: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    macd: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ema_20: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ema_50: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    stock: Mapped["Stock"] = relationship(back_populates="technical_indicators")


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    fii_activity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    dii_activity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    news_sentiment: Mapped[str | None] = mapped_column(Text, nullable=True)
    global_market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSettings(Base):
    """
    Singleton row (id is always 1) holding user-configurable, persisted app
    settings: the background auto-refresh cadence (null = disabled/"never")
    and the ranking composite-score weights, which the nightly digest job
    adapts over time based on trade outcomes.
    """
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    ranking_refresh_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_technical: Mapped[float] = mapped_column(Numeric, default=0.55)
    weight_sentiment: Mapped[float] = mapped_column(Numeric, default=0.30)
    weight_volume: Mapped[float] = mapped_column(Numeric, default=0.15)
    # Position sizing: quantity = capital * risk% / (entry - stop).
    capital: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    risk_per_trade_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # AI analyst practice on historical charts while the market is closed.
    ai_practice_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    # Which saved LLM profile serves chat/analyst notes vs. background work (news, forecasts, practice).
    chat_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    background_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Windows toast notifications for holding alerts (in addition to in-app/browser alerts).
    desktop_notifications: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    # "single" = one model (the background model), exactly the original behaviour;
    # "multi"  = every enabled model analyses independently, combined by the consensus engine.
    analysis_mode: Mapped[str] = mapped_column(String(10), default="single", server_default="single", nullable=False)
    # General settings (Settings -> General). Null = use the config/.env default, so they travel in a backup
    # once you change them and existing installs keep behaving exactly as before until you do.
    discovery_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    universe_screen_time: Mapped[str | None] = mapped_column(String(5), nullable=True)      # "HH:MM" IST
    auto_list_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_review_shortlist: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_traded_value_cr: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TradingMemory(Base):
    __tablename__ = "trading_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=True)
    similar_past_setups: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    accuracy_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class MorningBrief(Base):
    """One pre-market brief per trading day (PRD §13): today's best trade plus market context."""
    __tablename__ = "morning_briefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    brief_date: Mapped[date] = mapped_column(Date, unique=True, index=True, nullable=False)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True)
    market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    brief_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    recommendation: Mapped["Recommendation | None"] = relationship()


# ---------------------------------------------------------------------------
# AI analyst (Qwen) learning loop — see ai_analyst_service.py. The LLM's
# weights never change; what accumulates is its graded track record, its own
# written lessons, and its reads of the news, all fed back into its prompts.
# ---------------------------------------------------------------------------
class AINewsInsight(Base):
    """Qwen's read of one headline for one stock: impact -2..+2 with a reason."""
    __tablename__ = "ai_news_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    headline_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    published: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    impact: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    stock: Mapped["Stock"] = relationship()


class AIPrediction(Base):
    """Qwen's dated forecast for a stock over `horizon_days` trading days, graded later."""
    __tablename__ = "ai_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=5)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)          # up / down / flat
    probability_up: Mapped[float] = mapped_column(Numeric, nullable=False)       # 0-100
    # "live" = a real forecast made today; "practice" = a historical chart replay
    # (prediction_date is then the hidden historical date, graded immediately).
    kind: Mapped[str] = mapped_column(String(10), default="live", server_default="live", nullable=False, index=True)
    # primary = the forecast the app uses (single-model forecast or the daily consensus);
    # member  = one model's answer inside a multi-model run; adhoc = on-demand consensus.
    role: Mapped[str] = mapped_column(String(10), default="primary", server_default="primary", nullable=False, index=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consensus_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    context_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(10), nullable=True)        # BUY/HOLD/SELL/AVOID
    confidence: Mapped[float | None] = mapped_column(Numeric, nullable=True)             # 0-100
    entry: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    target: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(20), nullable=True)
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)               # full structured answer
    market_regime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # EvidenceEngine: share of the model's checkable claims the data supports (0-1; None = nothing checkable)
    evidence_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # Outcome from real bars over the horizon (None = not measurable / unknown)
    max_favorable_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    max_adverse_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    target_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stop_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome_pnl_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    expected_move_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_at_prediction: Mapped[float] = mapped_column(Numeric, nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(40), nullable=True)
    conviction_at_prediction: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    lessons_version: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Grading
    actual_return_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    actual_direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    stock: Mapped["Stock"] = relationship()


class AILesson(Base):
    """
    A versioned set of shared lessons, written by the background model after reviewing the graded
    forecasts of every model; every model (including newly added ones) reads the latest version.
    """
    __tablename__ = "ai_lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    lessons_text: Mapped[str] = mapped_column(Text, nullable=False)
    based_on_predictions: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate_at_creation: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)                 # who wrote them
    source_models: Mapped[str | None] = mapped_column(Text, nullable=True)               # whose graded forecasts
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AIMarketOutlook(Base):
    """Qwen's call on the next NSE session (NIFTY direction + sector impacts), graded afterwards."""
    __tablename__ = "ai_market_outlooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bias: Mapped[str] = mapped_column(String(10), nullable=False)               # up / down / flat
    probability_up: Mapped[float] = mapped_column(Numeric, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    sector_impacts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    headlines_considered: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(10), default="primary", server_default="primary", nullable=False)
    lessons_version: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actual_return_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Switchable LLM providers (llm_providers.py / llm_service.py)
# ---------------------------------------------------------------------------
class LLMProfile(Base):
    """A saved model connection: local (Bionic/LM Studio/Ollama) or a cloud API (Claude, Kimi, ...)."""
    __tablename__ = "llm_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)          # openai_compatible | anthropic
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored in the local database only; the API never returns it unmasked. "env:NAME" reads an env var.
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)            # requests/day; 0 = unlimited
    allow_practice: Mapped[bool] = mapped_column(Boolean, default=False)    # may run chart practice (many calls)
    usage_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    # Multi-model analysis (ai_orchestrator): profiles with enabled=True take part.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")   # lower runs/ranks first
    temperature: Mapped[float | None] = mapped_column(Numeric, nullable=True)            # None = provider default
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hourly_limit: Mapped[int] = mapped_column(Integer, default=0, server_default="0")    # 0 = no hourly cap
    # Optional prices (USD per 1M tokens) for the usage/cost estimate.
    input_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    output_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def is_local(self) -> bool:
        url = (self.base_url or "").lower()
        return self.kind != "anthropic" and ("localhost" in url or "127.0.0.1" in url)


# ---------------------------------------------------------------------------
# Holdings monitoring (position_service.py)
# ---------------------------------------------------------------------------
class Position(Base):
    """Shares the user actually holds, monitored continuously for loss risk, stop-loss and target."""
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)      # open | closed
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)                 # currently held
    avg_price: Mapped[float] = mapped_column(Numeric, nullable=False)                # average cost of held shares
    opened_on: Mapped[date] = mapped_column(Date, nullable=False)
    stop_loss: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    target: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Numeric, default=0)
    # Latest monitoring snapshot
    last_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    loss_risk: Mapped[float | None] = mapped_column(Numeric, nullable=True)          # 0-100
    risk_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)            # JSON list
    suggestion_json: Mapped[str | None] = mapped_column(Text, nullable=True)         # latest AI suggestion (JSON)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    stock: Mapped["Stock"] = relationship()
    transactions: Mapped[list["PositionTransaction"]] = relationship(
        back_populates="position", cascade="all, delete-orphan", order_by="PositionTransaction.id")
    alerts: Mapped[list["PositionAlert"]] = relationship(back_populates="position", cascade="all, delete-orphan")


class PositionTransaction(Base):
    __tablename__ = "position_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(4), nullable=False)          # BUY | SELL
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    price: Mapped[float] = mapped_column(Numeric, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    realized_pnl: Mapped[float | None] = mapped_column(Numeric, nullable=True)   # SELLs: (price - avg cost) x qty
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    position: Mapped["Position"] = relationship(back_populates="transactions")


class PositionAlert(Base):
    """An alert raised while monitoring a holding; risk warnings are graded later to learn their reliability."""
    __tablename__ = "position_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)     # info | warning | critical
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(Numeric, nullable=False)
    loss_risk: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # Grading (risk warnings only): did the price actually fall afterwards?
    outcome_return_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    position: Mapped["Position"] = relationship(back_populates="alerts")


# ---------------------------------------------------------------------------
# Multi-model research (research_context.py, ai_orchestrator.py, consensus_engine.py)
# ---------------------------------------------------------------------------
class ResearchContext(Base):
    """The exact, timestamped research package every model was given (so analyses are comparable and auditable)."""
    __tablename__ = "research_contexts"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id"), nullable=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    context_json: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    data_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AIConsensus(Base):
    """One multi-model run: every model's vote, the transparent agreement scores, and the combined view."""
    __tablename__ = "ai_consensus"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    context_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger: Mapped[str] = mapped_column(String(20), default="on_demand")      # daily | on_demand
    signal: Mapped[str] = mapped_column(String(10), nullable=False)            # BUY / HOLD / SELL
    probability_up: Mapped[float] = mapped_column(Numeric, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric, nullable=False)          # 0-100
    votes_json: Mapped[str] = mapped_column(Text, nullable=False)
    scores_json: Mapped[str] = mapped_column(Text, nullable=False)
    levels_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_json: Mapped[str] = mapped_column(Text, nullable=False)
    disagreement_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    models_used: Mapped[int] = mapped_column(Integer, default=0)
    models_failed_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    market_regime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    stock: Mapped["Stock"] = relationship()


class LLMUsage(Base):
    """Requests and tokens per model per hour — for hourly caps and the cost estimate."""
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    hour: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)   # UTC, truncated to the hour
    requests: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)


class ModelPreset(Base):
    """A saved model configuration (which models are enabled, mode, chat/background choice)."""
    __tablename__ = "model_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PatternStat(Base):
    """
    Evidence-backed knowledge: how often a verified factor (or combination) was followed by the
    move it implies, counted only from graded real outcomes — never from what a model said.
    """
    __tablename__ = "pattern_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(200), nullable=False)          # e.g. "breakout+volume_high"
    bias: Mapped[str] = mapped_column(String(10), nullable=False)              # bullish | bearish
    regime: Mapped[str] = mapped_column(String(40), default="ALL", nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), default="5d", nullable=False)
    # history = measured on past daily bars of the tracked stocks; live = from graded live analyses
    source: Mapped[str] = mapped_column(String(10), default="live", nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    total_return_pct: Mapped[float] = mapped_column(Numeric, default=0)
    source_models: Mapped[str | None] = mapped_column(Text, nullable=True)      # comma list of models that cited it
    first_observed: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_observed: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("pattern", "bias", "regime", "timeframe", "source", name="uq_pattern_stat"),)


class DataSource(Base):
    """
    A source the app reads for research (Settings -> Data sources). Built-in ones (the news feeds the
    app shipped with, NSE's official data) can be switched off but not deleted; feeds you add can be.
    """
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    # rss = a news feed (RSS/Atom); nse_filings / nse_calendar / nse_bhavcopy / nse_ban / nse_deals = NSE data
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NSEDaily(Base):
    """NSE's official end-of-day record per share (bhavcopy): close, volume and delivery."""
    __tablename__ = "nse_daily"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    series: Mapped[str] = mapped_column(String(4), nullable=False)
    prev_close: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    open: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    high: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    low: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deliv_qty: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deliv_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    __table_args__ = (UniqueConstraint("trade_date", "symbol", "series", name="uq_nse_daily"),)


class UniverseScreen(Base):
    """
    One daily discovery run: how many shares were screened, why the rest were filtered out, what
    the AI review said, and which shares made the Auto list (with each one's reason).
    """
    __tablename__ = "universe_screens"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="scheduled")
    pool_size: Mapped[int] = mapped_column(Integer, default=0)
    eligible: Mapped[int] = mapped_column(Integer, default=0)
    ai_reviewed: Mapped[int] = mapped_column(Integer, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    filtered_json: Mapped[str | None] = mapped_column(Text, nullable=True)     # {reason: count}
    selected_json: Mapped[str | None] = mapped_column(Text, nullable=True)     # [{symbol, score, ai, why}]
    added_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    removed_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    top_sectors: Mapped[str | None] = mapped_column(Text, nullable=True)
    filings_read: Mapped[int] = mapped_column(Integer, default=0, server_default="0")   # NSE filings the AI read
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    seconds: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class TraderProfile(Base):
    """Your trading style, measured from your recorded trades (trader_profile_service.py). Single row."""
    __tablename__ = "trader_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    trades_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
