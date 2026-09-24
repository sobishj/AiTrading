"""
Live ranking engine — the "find the best shares" core.

For every active watchlist stock it combines:
- the technical score (analysis_service: trend, momentum, structure, volume,
  relative strength vs NIFTY),
- news sentiment (market_service, curated keyword matching),
- a volume-confirmation component,
blended by the adaptive weights in app_settings, then adjusted by evidence:
- historical edge: how the detected setup performed on this stock's own history,
- learned edge: how that strategy has performed in graded/real trades,
- context tags: Earnings Momentum (results news + price follow-through) and
  Sector Rotation (stock leading one of the strongest sectors).

The watchlist is sorted by the resulting conviction score, every stock gets a
deterministic trade plan, and rank changes get a plain-English reason built
from what actually changed (no LLM call on the hot path).

Also owns the persisted AppSettings row (refresh cadence, adaptive weights,
position sizing) and weight recalibration. Nothing here retrains the LLM.
"""
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from analysis_service import (
    ALL_STRATEGIES, EARNINGS_MOMENTUM, NO_SETUP, PRICE_SETUPS, SECTOR_ROTATION,
    TechnicalSnapshot, TradePlan, analysis_service,
)
from config import settings as app_config
from data_provider import yahoo_provider
from llm_service import llm_service
from market_service import BENCHMARK, market_service
from models import AppSettings, Recommendation, Stock, TradeHistory, tracked_stock_filter
from utils.logger import get_logger

logger = get_logger(__name__)

# Fallback weights, used only to seed the AppSettings row the first time it's created.
DEFAULT_WEIGHT_TECHNICAL = 0.55
DEFAULT_WEIGHT_SENTIMENT = 0.30
DEFAULT_WEIGHT_VOLUME = 0.15

# Seed value for the first-ever app_settings row; after that the DB row is the
# source of truth, changed via PUT /api/settings/refresh-interval (or the UI).
DEFAULT_REFRESH_SECONDS = app_config.RANKING_REFRESH_INTERVAL

# Recalibration guardrails: never let a single run swing a weight wildly,
# and never let any component collapse to (near) zero or dominate.
RECALIBRATION_BLEND = 0.7  # weight given to the *previous* weights vs the new signal
WEIGHT_MIN = 0.10
WEIGHT_MAX = 0.70
WIN_OUTCOMES = {"target_hit", "win", "profit"}
LOSS_OUTCOMES = {"stop_hit", "loss"}

# Endpoints reuse a ranking younger than this instead of recomputing it; the
# background loop (main.py) always forces a fresh run on its own cadence.
RANKING_CACHE_TTL_SECONDS = 20
RANKING_MAX_AGE_FOR_READS = 300

# Evidence adjustments (conviction points).
MAX_HISTORICAL_EDGE = 5.0
HISTORICAL_BASELINE_WIN_RATE = 40.0  # 1.5-ATR stop vs 3-ATR target breaks even near 33%
MIN_BACKTEST_SIGNALS = 4
MAX_LEARNED_EDGE = 5.0
MIN_LEARNED_TRADES = 8
SECTOR_ROTATION_BONUS = 3.0
EARNINGS_BONUS = 3.0

# How many top BUY ideas the background loop records as recommendations
# (market memory) each run. Existing ones are reused while still valid.
TOP_RECOMMENDATIONS_PER_RUN = 5
RECOMMENDATION_REUSE_DAYS = 7


@dataclass
class RankedStock:
    stock_id: int
    symbol: str
    name: str
    conviction_score: float
    technical: TechnicalSnapshot
    sentiment: dict
    volume_score: float
    rank: int = 0
    previous_rank: Optional[int] = None
    change_reason: Optional[str] = None
    # --- v2 ---
    sector: Optional[str] = None
    strategy: str = NO_SETUP
    plan: Optional[TradePlan] = None
    base_score: float = 0.0
    adjustments: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @property
    def action(self) -> str:
        return self.plan.action if self.plan else "WAIT"


@dataclass
class RankingContext:
    """Shared state for one ranking run, reused for single-stock re-ranks between runs."""
    benchmark_return_20d: Optional[float] = None
    market_regime: str = "neutral"
    market_summary: str = ""
    top_sectors: list[str] = field(default_factory=list)
    strategy_stats: dict = field(default_factory=dict)
    weights: tuple[float, float, float] = (DEFAULT_WEIGHT_TECHNICAL, DEFAULT_WEIGHT_SENTIMENT, DEFAULT_WEIGHT_VOLUME)
    # AI analyst (Qwen) inputs — see ai_analyst_service.
    ai_news: dict = field(default_factory=dict)          # symbol -> AI news sentiment
    ai_forecasts: dict = field(default_factory=dict)     # symbol -> (probability_up, direction)
    ai_trust: float = 0.0                                # conviction points at full conviction


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class RankingService:
    def __init__(self) -> None:
        # symbol -> (rank, score, strategy, technical_score, sentiment_score)
        self._last_ranking: dict[str, tuple] = {}
        self._history: list[dict] = []
        self._recent_changes: list[dict] = []
        self._ranking_cache: list[RankedStock] = []
        self._ranking_cache_at: Optional[datetime] = None
        self._context = RankingContext()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Persisted settings (auto-refresh cadence, adaptive weights, sizing)
    # ------------------------------------------------------------------
    def get_or_create_settings(self, db: Session) -> AppSettings:
        cfg = db.get(AppSettings, 1)
        if cfg is None:
            cfg = AppSettings(
                id=1,
                ranking_refresh_seconds=DEFAULT_REFRESH_SECONDS,
                weight_technical=DEFAULT_WEIGHT_TECHNICAL,
                weight_sentiment=DEFAULT_WEIGHT_SENTIMENT,
                weight_volume=DEFAULT_WEIGHT_VOLUME,
                capital=app_config.DEFAULT_CAPITAL,
                risk_per_trade_pct=app_config.DEFAULT_RISK_PER_TRADE_PCT,
                updated_at=datetime.utcnow(),
            )
            db.add(cfg)
            db.commit()
            db.refresh(cfg)
            logger.info("Seeded default app_settings row (refresh=%ss)", DEFAULT_REFRESH_SECONDS)
        elif cfg.capital is None or cfg.risk_per_trade_pct is None:
            cfg.capital = cfg.capital or app_config.DEFAULT_CAPITAL
            cfg.risk_per_trade_pct = cfg.risk_per_trade_pct or app_config.DEFAULT_RISK_PER_TRADE_PCT
            db.commit()
            db.refresh(cfg)
        return cfg

    def update_refresh_interval(self, db: Session, seconds: Optional[int]) -> AppSettings:
        """seconds=None disables auto-refresh entirely ("never")."""
        cfg = self.get_or_create_settings(db)
        cfg.ranking_refresh_seconds = seconds
        cfg.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(cfg)
        logger.info("Auto-refresh interval updated to %s", "never" if seconds is None else f"{seconds}s")
        return cfg

    def update_risk_settings(self, db: Session, capital: float, risk_per_trade_pct: float) -> AppSettings:
        cfg = self.get_or_create_settings(db)
        cfg.capital = capital
        cfg.risk_per_trade_pct = risk_per_trade_pct
        cfg.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(cfg)
        return cfg

    def get_weights(self, db: Session) -> tuple[float, float, float]:
        cfg = self.get_or_create_settings(db)
        return float(cfg.weight_technical), float(cfg.weight_sentiment), float(cfg.weight_volume)

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------
    @staticmethod
    def _volume_component(technical: TechnicalSnapshot) -> float:
        """0-100 volume confirmation: 50 = average; heavy volume on a down day counts against."""
        vr = technical.volume_ratio
        if vr is None:
            return 50.0
        if technical.change_pct is not None and technical.change_pct < 0 and vr >= 1.5:
            return 30.0
        return round(_clamp(50.0 + (min(vr, 3.0) - 1.0) * 25.0, 20.0, 100.0), 2)

    def _composite_score(self, technical: TechnicalSnapshot, sentiment: dict,
                         weights: tuple[float, float, float]) -> tuple[float, float]:
        w_technical, w_sentiment, w_volume = weights
        volume_component = self._volume_component(technical)
        score = (
            technical.technical_score * w_technical
            + sentiment["score"] * w_sentiment
            + volume_component * w_volume
        )
        return round(score, 2), volume_component

    @staticmethod
    def _keywords(stock: Stock) -> list[str]:
        if stock.keywords:
            return [k.strip() for k in stock.keywords.split(",") if k.strip()]
        return [stock.name]

    async def rank_stock(self, stock: Stock, candle_provider=None,
                         weights: Optional[tuple[float, float, float]] = None,
                         context: Optional[RankingContext] = None) -> RankedStock:
        """Score one stock (technical + sentiment + volume, then evidence adjustments and a trade plan)."""
        context = context or self._context
        weights = weights or context.weights
        candle_provider = candle_provider or self.candle_provider
        candles = await candle_provider(stock)
        technical = analysis_service.compute_indicators(stock.symbol, candles, context.benchmark_return_20d)
        sentiment = market_service.stock_sentiment(stock.symbol, stock.name, self._keywords(stock))
        ai_news = context.ai_news.get(stock.symbol)
        if ai_news:
            # Qwen's reads of the headlines replace keyword counting when available.
            sentiment = {**sentiment, "score": ai_news["score"], "sentiment": ai_news["sentiment"],
                         "source": "ai", "ai_reads": ai_news["reads"],
                         "news_count": max(sentiment["news_count"], len(ai_news["reads"]))}
        base, volume_component = self._composite_score(technical, sentiment, weights)

        item = RankedStock(
            stock_id=stock.id, symbol=stock.symbol, name=stock.name, sector=stock.sector,
            conviction_score=base, base_score=base, technical=technical,
            sentiment=sentiment, volume_score=volume_component,
        )
        self._apply_strategy_and_evidence(item, context)
        return item

    def _apply_strategy_and_evidence(self, item: RankedStock, context: RankingContext) -> None:
        t, s = item.technical, item.sentiment
        adjustments: dict[str, float] = {}

        # Primary strategy, in priority order.
        strategy = NO_SETUP
        if (s.get("earnings_news") and s["score"] > 55 and (t.change_pct or 0) > 1.0
                and t.technical_score >= 50):
            strategy = EARNINGS_MOMENTUM
            adjustments["earnings_momentum"] = EARNINGS_BONUS
        elif t.setups:
            strategy = t.setups[0]
        elif (item.sector in context.top_sectors and (t.relative_strength_20d or 0) > 0
              and t.technical_score >= 55):
            strategy = SECTOR_ROTATION
        if item.sector in context.top_sectors and (t.relative_strength_20d or 0) > 0:
            item.tags.append(f"{item.sector} is a leading sector")
            adjustments["sector_rotation"] = SECTOR_ROTATION_BONUS
        item.strategy = strategy

        # Historical edge of this setup on this very stock.
        backtest_key = strategy if strategy in PRICE_SETUPS else (t.setups[0] if t.setups else None)
        stats = t.backtest.get(backtest_key) if backtest_key else None
        if stats and stats.signals >= MIN_BACKTEST_SIGNALS and stats.win_rate is not None:
            adjustments["historical_edge"] = round(_clamp(
                (stats.win_rate - HISTORICAL_BASELINE_WIN_RATE) / 6.0, -MAX_HISTORICAL_EDGE, MAX_HISTORICAL_EDGE), 2)

        # Learned edge from graded recommendations and real trades.
        learned = context.strategy_stats.get(strategy)
        if learned and learned.get("trades", 0) >= MIN_LEARNED_TRADES and learned.get("win_rate") is not None:
            adjustments["learned_edge"] = round(_clamp(
                (learned["win_rate"] - 50.0) / 5.0, -MAX_LEARNED_EDGE, MAX_LEARNED_EDGE), 2)

        # AI analyst view, weighted by the trust Qwen has earned from graded forecasts
        # (0 until it has a proven edge over the naive baseline).
        forecast = context.ai_forecasts.get(item.symbol)
        if forecast and context.ai_trust > 0:
            from ai_analyst_service import AIAnalystService  # local import: avoids a cycle
            adjustments["ai_view"] = AIAnalystService.conviction_adjustment(forecast[0], context.ai_trust)

        if not t.has_data:
            adjustments = {}
        item.adjustments = adjustments
        item.conviction_score = round(_clamp(item.base_score + sum(adjustments.values()), 0.0, 100.0), 2)
        item.plan = analysis_service.build_trade_plan(t, strategy, item.conviction_score, context.market_regime)

    # ------------------------------------------------------------------
    # Ranking run
    # ------------------------------------------------------------------
    @staticmethod
    async def candle_provider(stock: Stock):
        return await market_service.get_candles_for_symbol(stock.symbol, days=app_config.HISTORY_DAYS)

    async def _build_context(self, db: Session) -> RankingContext:
        from learning_service import learning_service  # local import: learning imports ranking

        weights = self.get_weights(db)
        benchmark_candles, overview, _ = await asyncio.gather(
            yahoo_provider.get_candles(BENCHMARK, days=app_config.HISTORY_DAYS),
            market_service.get_market_overview(),
            market_service.fetch_news_async(),
        )
        benchmark = analysis_service.compute_indicators("NIFTY 50", benchmark_candles)
        from ai_analyst_service import ai_analyst_service  # local import: it imports ranking helpers lazily
        perf = ai_analyst_service.performance(db)
        return RankingContext(
            benchmark_return_20d=benchmark.return_20d,
            market_regime=overview["regime"],
            market_summary=overview["summary"],
            strategy_stats={row["strategy"]: row for row in learning_service.strategy_performance(db)},
            weights=weights,
            ai_news=ai_analyst_service.news_sentiment_map(db),
            ai_forecasts={symbol: (float(p.probability_up), p.direction)
                          for symbol, p in ai_analyst_service.todays_predictions(db).items()},
            ai_trust=perf["trust_weight"],
        )

    @staticmethod
    def _leading_sectors(ranked: list[RankedStock], top_n: int = 2) -> list[str]:
        """Sectors (2+ stocks) with the best average 20-day relative strength, if positive."""
        by_sector: dict[str, list[float]] = defaultdict(list)
        for item in ranked:
            if item.sector and item.technical.relative_strength_20d is not None:
                by_sector[item.sector].append(item.technical.relative_strength_20d)
        averages = {sector: sum(v) / len(v) for sector, v in by_sector.items() if len(v) >= 2}
        leaders = sorted(averages, key=averages.get, reverse=True)[:top_n]
        return [s for s in leaders if averages[s] > 0]

    def _cache_age_seconds(self) -> Optional[float]:
        if self._ranking_cache_at is None:
            return None
        return (datetime.utcnow() - self._ranking_cache_at).total_seconds()

    def _cache_is_fresh(self, max_age: float = RANKING_CACHE_TTL_SECONDS) -> bool:
        age = self._cache_age_seconds()
        return age is not None and age < max_age

    def get_cached_ranked_stock(self, symbol: str, max_age: float = RANKING_MAX_AGE_FOR_READS) -> Optional[RankedStock]:
        if not self._cache_is_fresh(max_age):
            return None
        return next((item for item in self._ranking_cache if item.symbol == symbol), None)

    @property
    def last_ranked_at(self) -> Optional[datetime]:
        return self._ranking_cache_at

    @property
    def context(self) -> RankingContext:
        return self._context

    async def run_full_ranking(self, db: Session, force: bool = False,
                               max_age: float = RANKING_CACHE_TTL_SECONDS) -> list[RankedStock]:
        """
        Re-rank every tracked stock (Auto universe + Manual list). Concurrent callers share one run
        (lock), and results younger than `max_age` are reused unless forced.
        """
        if not force and self._cache_is_fresh(max_age):
            return self._ranking_cache

        async with self._lock:
            # Another caller may have finished a run while we waited.
            if not force and self._cache_is_fresh(max_age):
                return self._ranking_cache
            started = datetime.utcnow()

            context = await self._build_context(db)
            stocks = db.query(Stock).filter(tracked_stock_filter()).all()
            ranked = list(await asyncio.gather(
                *(self.rank_stock(stock, self.candle_provider, context.weights, context) for stock in stocks)
            ))

            # Sector rotation needs the whole universe, so tag it in a second pass.
            context.top_sectors = self._leading_sectors(ranked)
            if context.top_sectors:
                for item in ranked:
                    item.tags.clear()
                    self._apply_strategy_and_evidence(item, context)
            self._context = context

            result = self.rerank_watchlist(ranked)

            prices = {item.stock_id: item.technical.close for item in result if item.technical.close is not None}
            for stock in stocks:
                if stock.id in prices:
                    stock.current_price = round(prices[stock.id], 2)
                    stock.last_updated = datetime.utcnow()
            db.commit()

            self._ranking_cache = result
            self._ranking_cache_at = datetime.utcnow()
            logger.info("Ranked %d stocks in %.1fs (regime=%s, leading sectors=%s); top: %s",
                        len(result), (self._ranking_cache_at - started).total_seconds(),
                        context.market_regime, context.top_sectors or "none",
                        ", ".join(f"{r.symbol}({r.conviction_score:.0f},{r.action})" for r in result[:3]) or "n/a")
            return result

    def rerank_watchlist(self, ranked_stocks: list[RankedStock]) -> list[RankedStock]:
        """Sort by conviction (BUY ideas first on ties), assign ranks, and explain rank moves."""
        action_order = {"BUY": 0, "WAIT": 1, "AVOID": 2}
        ranked_stocks.sort(key=lambda r: (-r.conviction_score, action_order.get(r.action, 3), r.symbol))

        now = datetime.utcnow().isoformat()
        for idx, item in enumerate(ranked_stocks, start=1):
            item.rank = idx
            prev = self._last_ranking.get(item.symbol)
            if prev:
                item.previous_rank = prev[0]
                if item.previous_rank != item.rank:
                    item.change_reason = self._explain_change(item, prev)
                    self._recent_changes.append({
                        "timestamp": now, "symbol": item.symbol, "old_rank": item.previous_rank,
                        "new_rank": item.rank, "reason": item.change_reason,
                    })

        self._recent_changes = self._recent_changes[-100:]
        self._last_ranking = {
            item.symbol: (item.rank, item.conviction_score, item.strategy,
                          item.technical.technical_score, item.sentiment["score"])
            for item in ranked_stocks
        }
        self._history.append({
            "timestamp": now,
            "ranking": [(item.symbol, item.rank, item.conviction_score) for item in ranked_stocks],
        })
        self._history = self._history[-500:]
        return ranked_stocks

    @staticmethod
    def _explain_change(item: RankedStock, prev: tuple) -> str:
        """Plain-English reason for a rank move, from the components that actually changed."""
        old_rank, old_score, old_strategy, old_tech, old_sent = prev
        direction = "Up" if item.rank < old_rank else "Down"
        reasons = []
        if item.strategy != old_strategy and item.strategy != NO_SETUP:
            reasons.append(f"new {item.strategy} setup")
        elif item.strategy != old_strategy:
            reasons.append(f"{old_strategy} setup no longer valid")
        tech = item.technical.technical_score
        if abs(tech - old_tech) >= 3:
            reasons.append(f"technical score {old_tech:.0f}→{tech:.0f}")
        sent = item.sentiment["score"]
        if abs(sent - old_sent) >= 5:
            headline = item.sentiment.get("matched_headlines", [None])[0] if item.sentiment.get("matched_headlines") else None
            reasons.append(f"news turned {item.sentiment['sentiment']}" + (f" (\"{headline}\")" if headline else ""))
        if item.technical.volume_ratio and item.technical.volume_ratio >= 1.5:
            reasons.append(f"volume {item.technical.volume_ratio:.1f}x average")
        if not reasons:
            reasons.append(f"conviction {old_score:.0f}→{item.conviction_score:.0f} as other stocks moved")
        return f"{direction} from #{old_rank} to #{item.rank}: " + "; ".join(reasons) + "."

    @staticmethod
    def score_drivers(item: RankedStock) -> list[str]:
        """Top reasons a stock ranks where it does, for reasoning text and chat context."""
        drivers = list(item.technical.positives[:4])
        if item.sentiment["news_count"]:
            drivers.append(f"news sentiment {item.sentiment['sentiment']} ({item.sentiment['news_count']} headlines)")
        drivers += item.tags
        return drivers

    def get_ranking_history(self, limit: int = 50) -> list[dict]:
        return self._history[-limit:]

    def get_recent_changes(self, limit: int = 10) -> list[dict]:
        return self._recent_changes[-limit:]

    @staticmethod
    def get_best_pick(ranked_stocks: list[RankedStock]) -> Optional[RankedStock]:
        """Highest-conviction actionable (BUY) idea; None when nothing qualifies today."""
        return next((item for item in ranked_stocks if item.action == "BUY"), None)

    @staticmethod
    def build_ranking_broadcast_payload(ranked: list[RankedStock], trigger: Optional[str] = None) -> dict:
        """Shared WebSocket payload shape (used by /api/refresh and the background loops)."""
        best = RankingService.get_best_pick(ranked)
        return {
            "type": "ranking_update",
            "timestamp": datetime.utcnow().isoformat(),
            "trigger": trigger,
            "best_pick": best.symbol if best else None,
            "ranking": [
                {"symbol": r.symbol, "rank": r.rank, "conviction_score": r.conviction_score,
                 "action": r.action, "strategy": r.strategy, "change_reason": r.change_reason}
                for r in ranked
            ],
        }

    # ------------------------------------------------------------------
    # Trade plans and recommendations (market memory)
    # ------------------------------------------------------------------
    @staticmethod
    def position_size(plan: TradePlan, cfg: AppSettings) -> dict:
        capital = float(cfg.capital or app_config.DEFAULT_CAPITAL)
        risk_pct = float(cfg.risk_per_trade_pct or app_config.DEFAULT_RISK_PER_TRADE_PCT)
        per_share_risk = plan.entry_mid - plan.stop_loss
        if per_share_risk <= 0:
            return {"quantity": 0, "capital_required": 0.0, "risk_amount": 0.0,
                    "capital": capital, "risk_per_trade_pct": risk_pct}
        quantity = int((capital * risk_pct / 100) // per_share_risk)
        quantity = max(0, min(quantity, int(capital // plan.entry_mid)))
        return {
            "quantity": quantity,
            "capital_required": round(quantity * plan.entry_mid, 2),
            "risk_amount": round(quantity * per_share_risk, 2),
            "capital": capital,
            "risk_per_trade_pct": risk_pct,
        }

    def build_reasoning(self, item: RankedStock) -> str:
        """Deterministic, always-available reasoning text for a plan (the LLM may add commentary later)."""
        t, plan = item.technical, item.plan
        if plan is None:
            return "No price data available to build a plan."
        drivers = self.score_drivers(item)
        lead = {
            "BUY": f"{plan.strategy} setup with conviction {item.conviction_score:.0f}/100.",
            "WAIT": ("No clean entry signal yet" if item.strategy == NO_SETUP else f"{item.strategy} forming")
                    + f" — conviction {item.conviction_score:.0f}/100 is below the buy threshold.",
            "AVOID": f"Avoid fresh longs: {t.trend} with technical score {t.technical_score:.0f}/100.",
        }[plan.action]
        parts = [lead]
        if drivers:
            parts.append("Supporting: " + "; ".join(drivers[:4]) + ".")
        if t.negatives:
            parts.append("Against: " + "; ".join(t.negatives[:3]) + ".")
        stats = t.backtest.get(item.strategy) if item.strategy in PRICE_SETUPS else None
        if stats and stats.signals:
            parts.append(f"On this stock, past {item.strategy} signals won {stats.win_rate:.0f}% of "
                         f"{stats.signals} trades (avg {stats.avg_return_pct:+.1f}%).")
        if self._context.market_regime == "risk-off" and plan.action == "BUY":
            parts.append("Market regime is risk-off — consider half size.")
        return " ".join(parts)

    def plan_payload(self, item: RankedStock, cfg: AppSettings,
                     rec: Optional[Recommendation] = None) -> Optional[dict]:
        """API shape for the Trade Plan panel."""
        plan = item.plan
        if plan is None:
            return None
        sizing = self.position_size(plan, cfg) if plan.action != "AVOID" else {
            "quantity": 0, "capital_required": 0.0, "risk_amount": 0.0,
            "capital": float(cfg.capital or 0), "risk_per_trade_pct": float(cfg.risk_per_trade_pct or 0)}
        return {
            "symbol": item.symbol,
            "name": item.name,
            "rank": item.rank,
            "action": plan.action,
            "instrument": plan.instrument,
            "strategy": plan.strategy,
            "entry_low": plan.entry_low,
            "entry_high": plan.entry_high,
            "entry": plan.entry_mid,
            "target": plan.target,
            "stop_loss": plan.stop_loss,
            "risk_reward": plan.risk_reward,
            "holding_period": plan.holding_period,
            "risk_level": plan.risk_level,
            "confidence_score": item.conviction_score,
            "current_price": item.technical.close,
            "change_pct": item.technical.change_pct,
            "reasoning": rec.reasoning if rec is not None else self.build_reasoning(item),
            "ai_commentary": rec.ai_commentary if rec is not None else None,
            "invalidation": plan.invalidation,
            "exit_logic": plan.exit_logic,
            "recommendation_id": rec.id if rec is not None else None,
            "generated_at": (rec.timestamp if rec is not None else self._ranking_cache_at or datetime.utcnow()).isoformat(),
            "market_regime": self._context.market_regime,
            **sizing,
        }

    def _context_json(self, item: RankedStock) -> str:
        t = item.technical
        return json.dumps({
            "technical": {k: getattr(t, k) for k in (
                "close", "change_pct", "rsi", "macd", "macd_signal", "ema_20", "ema_50", "ema_200", "atr",
                "atr_pct", "volume_ratio", "support", "resistance", "high_52w", "return_20d",
                "relative_strength_20d", "trend", "technical_score", "setups")},
            "score_breakdown": t.score_breakdown,
            "positives": t.positives,
            "negatives": t.negatives,
            "sentiment": {k: item.sentiment.get(k) for k in ("sentiment", "score", "news_count", "headlines")},
            "backtest": {k: vars(v) for k, v in t.backtest.items()},
            "adjustments": item.adjustments,
            "tags": item.tags,
            "rank": item.rank,
            "market_regime": self._context.market_regime,
            "market_summary": self._context.market_summary,
            "weights": self._context.weights,
        }, default=str)

    async def generate_recommendation(self, db: Session, item: RankedStock,
                                      holding_period: Optional[str] = None) -> Optional[Recommendation]:
        """
        Persist a BUY idea as a recommendation — or return the existing one if
        it's still the same trade (same strategy, under a week old, price still
        near its entry zone and above its stop). Returns None for WAIT/AVOID
        and when there's no price data: only actionable calls enter market
        memory, so learning isn't polluted by non-trades.
        """
        plan = item.plan
        if plan is None or plan.action != "BUY" or not item.technical.has_data:
            return None

        latest = (
            db.query(Recommendation)
            .filter(Recommendation.stock_id == item.stock_id)
            .order_by(Recommendation.timestamp.desc())
            .first()
        )
        if latest is not None and self._still_valid(latest, item):
            return latest

        rec = Recommendation(
            stock_id=item.stock_id,
            entry_price=plan.entry_mid, target_price=plan.target, stop_loss=plan.stop_loss,
            holding_period=holding_period or plan.holding_period, risk_level=plan.risk_level,
            reasoning=self.build_reasoning(item), timestamp=datetime.utcnow(),
            confidence_score=item.conviction_score,
            technical_score=item.technical.technical_score,
            sentiment_score=item.sentiment["score"],
            volume_score=item.volume_score,
            action=plan.action, instrument=plan.instrument, strategy=plan.strategy,
            entry_low=plan.entry_low, entry_high=plan.entry_high, risk_reward=plan.risk_reward,
            context_json=self._context_json(item),
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        logger.info("New recommendation #%s: %s BUY %s entry %.2f-%.2f SL %.2f T %.2f",
                    rec.id, item.symbol, plan.strategy, plan.entry_low, plan.entry_high, plan.stop_loss, plan.target)
        self._enqueue_commentary(rec.id, item)
        return rec

    @staticmethod
    def _still_valid(rec: Recommendation, item: RankedStock) -> bool:
        if rec.action not in (None, "BUY"):
            return False
        if datetime.utcnow() - rec.timestamp > timedelta(days=RECOMMENDATION_REUSE_DAYS):
            return False
        # Deliberately no strategy check: intraday volume can flip a setup's
        # label (e.g. Breakout <-> Sector Rotation) while it's the same trade.
        close = item.technical.close
        atr = item.technical.atr or close * 0.02
        entry_high = float(rec.entry_high if rec.entry_high is not None else rec.entry_price)
        return close > float(rec.stop_loss) and close <= entry_high + atr and close < float(rec.target_price)

    def _enqueue_commentary(self, rec_id: int, item: RankedStock) -> None:
        plan = item.plan
        stats = item.technical.backtest.get(item.strategy) if item.strategy in PRICE_SETUPS else None
        prompt_data = {
            "symbol": item.symbol, "action": plan.action, "strategy": plan.strategy,
            "market_regime": self._context.market_regime,
            "entry": f"{plan.entry_low:.2f}-{plan.entry_high:.2f}", "stop_loss": plan.stop_loss,
            "target": plan.target, "risk_reward": plan.risk_reward, "holding_period": plan.holding_period,
            "risk_level": plan.risk_level, "drivers": "; ".join(self.score_drivers(item)) or "n/a",
            "news": "; ".join(item.sentiment.get("matched_headlines", [])[:3]) or "no stock-specific news",
            "backtest": (f"{stats.win_rate:.0f}% win rate over {stats.signals} past signals"
                         if stats and stats.signals else "no history for this setup"),
        }

        async def job() -> None:
            from database import db_session  # local import: avoid cycles at module load
            text = await llm_service.generate_recommendation_reasoning(prompt_data)
            if not text:
                return
            with db_session() as db:
                rec = db.get(Recommendation, rec_id)
                if rec is not None:
                    rec.ai_commentary = text

        llm_service.enqueue(job, label=f"commentary:{item.symbol}")

    async def persist_top_recommendations(self, db: Session, ranked: list[RankedStock],
                                          limit: int = TOP_RECOMMENDATIONS_PER_RUN) -> list[Recommendation]:
        recs = []
        for item in [r for r in ranked if r.action == "BUY"][:limit]:
            rec = await self.generate_recommendation(db, item)
            if rec is not None:
                recs.append(rec)
        return recs

    # ------------------------------------------------------------------
    # Adaptive weight recalibration (run by the learning cycle)
    # ------------------------------------------------------------------
    def recalibrate_weights(self, db: Session, min_samples: int = 15) -> dict:
        """
        Nudge weight_technical/sentiment/volume based on which component score
        actually separated winning recommendations from losing ones. Heuristic,
        not ML: for each component, (mean score among wins) - (mean among
        losses); a bigger gap earns more weight. Blended 70/30 with the previous
        weights and clamped, so one noisy batch can't swing the model. When a
        recommendation has both an auto-graded outcome and a real Zerodha trade,
        the real trade wins.
        """
        rows = (
            db.query(TradeHistory, Recommendation)
            .join(Recommendation, TradeHistory.recommendation_id == Recommendation.id)
            .filter(TradeHistory.actual_outcome.isnot(None))
            .filter(Recommendation.technical_score.isnot(None))
            .order_by(TradeHistory.timestamp.desc())
            .limit(400)
            .all()
        )
        by_rec: dict[int, tuple] = {}
        for trade, rec in rows:
            current = by_rec.get(rec.id)
            if current is None or (trade.source == "zerodha" and current[0].source != "zerodha"):
                by_rec[rec.id] = (trade, rec)
        pairs = list(by_rec.values())[:200]

        cfg = self.get_or_create_settings(db)
        previous = {
            "technical": float(cfg.weight_technical),
            "sentiment": float(cfg.weight_sentiment),
            "volume": float(cfg.weight_volume),
        }

        sums = {"technical": {"win": [], "loss": []}, "sentiment": {"win": [], "loss": []},
                "volume": {"win": [], "loss": []}}
        for trade, rec in pairs:
            outcome = (trade.actual_outcome or "").lower()
            bucket = "win" if outcome in WIN_OUTCOMES else "loss" if outcome in LOSS_OUTCOMES else None
            if bucket is None:
                continue
            sums["technical"][bucket].append(float(rec.technical_score))
            sums["sentiment"][bucket].append(float(rec.sentiment_score or 50.0))
            sums["volume"][bucket].append(float(rec.volume_score or 50.0))

        sample_size = len(sums["technical"]["win"]) + len(sums["technical"]["loss"])
        if sample_size < min_samples:
            logger.info("Skipping weight recalibration: only %d graded+linked trades (need %d)",
                        sample_size, min_samples)
            return {"status": "insufficient_data", "sample_size": sample_size,
                    "previous_weights": previous, "new_weights": None}

        def separation(component: str) -> float:
            wins, losses = sums[component]["win"], sums[component]["loss"]
            if not wins or not losses:
                return 0.0
            return max((sum(wins) / len(wins)) - (sum(losses) / len(losses)), 0.0)

        raw = {c: max(separation(c), 0.01) for c in ("technical", "sentiment", "volume")}
        total = sum(raw.values())
        normalized = {c: v / total for c, v in raw.items()}

        blended = {
            c: RECALIBRATION_BLEND * previous[c] + (1 - RECALIBRATION_BLEND) * normalized[c]
            for c in ("technical", "sentiment", "volume")
        }
        clamped = {c: _clamp(v, WEIGHT_MIN, WEIGHT_MAX) for c, v in blended.items()}
        clamp_total = sum(clamped.values())
        final = {c: round(v / clamp_total, 4) for c, v in clamped.items()}

        cfg.weight_technical = final["technical"]
        cfg.weight_sentiment = final["sentiment"]
        cfg.weight_volume = final["volume"]
        cfg.updated_at = datetime.utcnow()
        db.commit()

        logger.info("Recalibrated weights from %s to %s (sample_size=%d)", previous, final, sample_size)
        return {"status": "recalibrated", "sample_size": sample_size,
                "previous_weights": previous, "new_weights": final}


ranking_service = RankingService()

__all__ = ["ranking_service", "RankingService", "RankedStock", "ALL_STRATEGIES"]
