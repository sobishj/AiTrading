"""
SQLAlchemy ORM models mirroring config/database.sql.
"""
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
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
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="stock", cascade="all, delete-orphan")
    trade_history: Mapped[list["TradeHistory"]] = relationship(back_populates="stock", cascade="all, delete-orphan")
    technical_indicators: Mapped[list["TechnicalIndicators"]] = relationship(back_populates="stock", cascade="all, delete-orphan")


def tracked_stock_filter():
    """Stocks the engine ranks and watches: the Auto universe plus the user's Manual list."""
    return or_(Stock.watchlist_status == "active", Stock.in_manual_list.is_(True))


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
    """A versioned set of lessons Qwen wrote after reviewing its graded predictions."""
    __tablename__ = "ai_lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    lessons_text: Mapped[str] = mapped_column(Text, nullable=False)
    based_on_predictions: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate_at_creation: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
    lessons_version: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actual_return_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
