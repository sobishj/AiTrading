"""
Turns a RankedStock into the explanations the UI and chat need:
- the PRD §9 Analysis panel sections (market context, technical confirmation,
  news, risk factors, historical comparisons, exit logic), all deterministic;
- an optional LLM analyst note grounded only in those sections (cached);
- the live context block the Trading Coach chat is grounded in.
"""
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from analysis_service import NO_SETUP, PRICE_SETUPS
from learning_service import learning_service
from llm_service import llm_service
from models import Recommendation, Stock, TradeHistory, UserChat, tracked_stock_filter
from ranking_service import RankedStock, ranking_service

NARRATIVE_CACHE_SECONDS = 600
# Kept short on purpose: a small local model copies numbers from earlier answers.
CHAT_HISTORY_TURNS = 4

ADJUSTMENT_LABELS = {
    "historical_edge": "Historical edge of this setup on this stock",
    "learned_edge": "Learned edge from graded trades",
    "sector_rotation": "Sector rotation bonus",
    "earnings_momentum": "Earnings momentum bonus",
    "ai_view": "AI analyst (Qwen) view, weighted by its track record",
    "timing": "Entry timing (dip in a long-term uptrend vs. short-term overextension)",
    "results_risk": "Results due within a week (NSE calendar) — gap risk",
    "fo_ban": "In NSE's F&O ban list (crowded positioning)",
    "delivery": "Move backed by unusually high delivery (NSE bhavcopy)",
}


CHAT_IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> date:
    return datetime.now(CHAT_IST).date()


def ist_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of an IST calendar day as naive UTC datetimes (how chat timestamps are stored)."""
    start = datetime.combine(day, datetime.min.time(), CHAT_IST).astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


def chat_days(db: Session) -> list[str]:
    """IST dates (YYYY-MM-DD) that have at least one chat message, newest first."""
    stamps = db.query(UserChat.timestamp).all()
    days = {(ts.replace(tzinfo=timezone.utc).astimezone(CHAT_IST)).date().isoformat() for (ts,) in stamps}
    return sorted(days, reverse=True)


class InsightService:
    def __init__(self) -> None:
        self._narratives: dict[str, tuple[float, Optional[datetime], str]] = {}

    # ------------------------------------------------------------------
    # Analysis panel
    # ------------------------------------------------------------------
    def build_analysis(self, db: Session, item: RankedStock) -> dict:
        t, s, plan = item.technical, item.sentiment, item.plan
        context = ranking_service.context
        strategy_stats = {row["strategy"]: row for row in learning_service.strategy_performance(db)}

        if not t.has_data:
            summary = f"No price data for {item.symbol} yet — check the symbol or data provider."
        else:
            summary = ranking_service.build_reasoning(item)

        risks = list(t.negatives)
        if context.market_regime == "risk-off":
            risks.insert(0, "Market regime is risk-off: broad selling can override stock-level setups.")
        if t.high_52w and t.close and t.close < t.high_52w and plan and plan.target > t.high_52w:
            risks.append(f"Target sits above the 52-week high ({t.high_52w:,.2f}), which may act as resistance.")
        if s["sentiment"] == "negative":
            risks.append("Recent news flow is negative.")
        if plan and plan.risk_level == "high":
            risks.append("High risk rating: volatile stock or low conviction — size down.")
        if not risks:
            risks.append("No major red flags in the data; standard stop-loss discipline applies.")

        historical_notes = []
        backtest_rows = []
        for name in PRICE_SETUPS:
            stats = t.backtest.get(name)
            if stats is None:
                continue
            backtest_rows.append(vars(stats))
            if name == item.strategy and stats.signals:
                historical_notes.append(
                    f"On {item.symbol}'s last ~year of daily bars, {name} signals fired {stats.signals} times: "
                    f"{stats.win_rate:.0f}% reached a 2R target before the stop, avg {stats.avg_return_pct:+.1f}%."
                )
        learned = strategy_stats.get(item.strategy)
        if learned and learned["trades"]:
            historical_notes.append(
                f"Across AiTrading's graded calls and your trades, {item.strategy} has won "
                f"{learned['win_rate']:.0f}% of {learned['trades']} trades (avg {learned['avg_return_pct']:+.1f}%)."
            )
        past_recs = (
            db.query(Recommendation).filter(Recommendation.stock_id == item.stock_id)
            .order_by(Recommendation.timestamp.desc()).limit(5).all()
        )
        outcomes = {
            th.recommendation_id: th.actual_outcome
            for th in db.query(TradeHistory).filter(
                TradeHistory.recommendation_id.in_([r.id for r in past_recs] or [-1])).all()
        }
        for rec in past_recs:
            historical_notes.append(
                f"{rec.timestamp:%d %b %Y}: {rec.action or 'BUY'} {rec.strategy or ''} at "
                f"{float(rec.entry_price):,.2f} → {outcomes.get(rec.id, 'open').replace('_', ' ')}."
            )
        if not historical_notes:
            historical_notes.append("No historical signals or past calls for this stock yet.")

        exit_logic = []
        if plan:
            exit_logic = [
                f"Stop-loss {plan.stop_loss:,.2f}: {plan.invalidation}",
                f"Target {plan.target:,.2f} ({plan.risk_reward:.1f}R).",
                plan.exit_logic,
            ]

        adjustments = {ADJUSTMENT_LABELS.get(k, k): v for k, v in item.adjustments.items()}
        return {
            "symbol": item.symbol,
            "name": item.name,
            "rank": item.rank or None,
            "action": item.action,
            "strategy": item.strategy,
            "conviction_score": item.conviction_score,
            "technical_score": t.technical_score,
            "sentiment_score": s["score"],
            "summary": summary,
            "market_context": context.market_summary or "Market context loading…",
            "technical_confirmation": t.positives or ["No bullish technical confirmation right now."],
            "concerns": t.negatives,
            "news": s.get("headlines", []),
            "news_sentiment": (f"{s['sentiment'].capitalize()} — {s['news_count']} recent headline(s) mention "
                               f"{item.name}." if s["news_count"] else f"No {item.name}-specific news in the last 72 hours."),
            "risk_factors": risks,
            "historical": backtest_rows,
            "historical_notes": historical_notes,
            "exit_logic": exit_logic,
            "score_breakdown": t.score_breakdown,
            "adjustments": adjustments,
            "ai_view": self._ai_view(db, item),
            "generated_at": datetime.utcnow(),
            "analysis": summary,
        }

    @staticmethod
    def _ai_view(db: Session, item: RankedStock) -> dict:
        """Qwen's latest forecast for this stock, its news reads, and how far its record says to trust it."""
        from ai_analyst_service import HORIZON_DAYS, MIN_GRADED_FOR_TRUST, ai_analyst_service

        prediction = ai_analyst_service.todays_predictions(db).get(item.symbol)
        perf = ai_analyst_service.performance(db)
        outlook = ai_analyst_service.outlook_payload(ai_analyst_service.latest_outlook(db))
        sector_impact = next((s for s in (outlook or {}).get("sector_impacts", []) if s["sector"] == item.sector), None)
        return {
            "outlook": None if outlook is None else {
                "session_date": outlook["session_date"], "bias": outlook["bias"],
                "probability_up": outlook["probability_up"], "summary": outlook["summary"],
                "sector_impact": sector_impact,
            },
            "forecast": None if prediction is None else {
                "date": prediction.prediction_date.isoformat(),
                "direction": prediction.direction,
                "probability_up": float(prediction.probability_up),
                "expected_move_pct": float(prediction.expected_move_pct) if prediction.expected_move_pct is not None else None,
                "reason": prediction.reason,
                "horizon_days": prediction.horizon_days,
                "lessons_version": prediction.lessons_version,
                "model": prediction.model,
            },
            "news_reads": item.sentiment.get("ai_reads", []),
            "track_record": ai_analyst_service.track_record_text(db),
            "trust_weight": perf["trust_weight"],
            "in_shadow_mode": perf["trust_weight"] == 0,
            "shadow_reason": (f"The AI analyst's forecasts don't affect the ranking until {MIN_GRADED_FOR_TRUST} have been graded "
                              f"and it beats the naive baseline ({perf['graded']} graded so far; each takes "
                              f"{HORIZON_DAYS} trading days).") if perf["trust_weight"] == 0 else None,
        }

    async def narrative(self, db: Session, item: RankedStock) -> Optional[str]:
        """LLM analyst note, cached per symbol until the next ranking run (max 10 min)."""
        cached = self._narratives.get(item.symbol)
        ranked_at = ranking_service.last_ranked_at
        if cached and cached[1] == ranked_at and time.monotonic() - cached[0] < NARRATIVE_CACHE_SECONDS:
            return cached[2]
        if not await llm_service.is_available("chat"):
            return None

        a = self.build_analysis(db, item)
        plan = item.plan
        text = await llm_service.analyze_stock({
            "symbol": item.symbol, "name": item.name, "sector": item.sector or "Unknown",
            "action": item.action, "strategy": item.strategy, "conviction": f"{item.conviction_score:.0f}",
            "rank": item.rank or "n/a",
            "market_context": a["market_context"],
            "technical": "; ".join(a["technical_confirmation"][:5]),
            "news": "; ".join(h["title"] for h in a["news"][:3]) or "no stock-specific news",
            "risks": "; ".join(a["risk_factors"][:4]),
            "history": " ".join(a["historical_notes"][:2]),
            "plan": (f"{plan.action} entry {plan.entry_low:.2f}-{plan.entry_high:.2f}, stop {plan.stop_loss:.2f}, "
                     f"target {plan.target:.2f}, {plan.holding_period}" if plan else "none"),
        })
        if text:
            self._narratives[item.symbol] = (time.monotonic(), ranked_at, text)
        return text or None

    # ------------------------------------------------------------------
    # Chat grounding
    # ------------------------------------------------------------------
    def chat_history(self, db: Session, limit: int = CHAT_HISTORY_TURNS) -> list[tuple[str, str, Optional[str]]]:
        """
        Today's recent (user, assistant, "Name (SYMBOL)" that was open) turns, oldest first.
        Chat is per day: each day starts a fresh conversation, so yesterday's prices and
        answers never leak into today's.
        """
        start, end = ist_day_bounds(today_ist())
        rows = (db.query(UserChat).filter(UserChat.timestamp >= start, UserChat.timestamp < end)
                .order_by(UserChat.timestamp.desc()).limit(limit).all())
        symbols = {r.stock_context for r in rows if r.stock_context}
        names = {s.symbol: s.name for s in db.query(Stock).filter(Stock.symbol.in_(symbols)).all()} if symbols else {}
        return [
            (r.user_message, r.ai_response,
             f"{names.get(r.stock_context, r.stock_context)} ({r.stock_context})" if r.stock_context else None)
            for r in reversed(rows)
        ]

    def chat_context(self, db: Session, ranked: list[RankedStock], selected: Optional[RankedStock]) -> list[str]:
        context = ranking_service.context
        lines = [f"Time: {datetime.now():%a %d %b %Y %H:%M} IST. Market: {context.market_summary}"]

        # The stock the user has open goes first and in full, so questions like
        # "how is this share?" are answered about it, not about the #1 pick.
        if selected is not None:
            t, plan = selected.technical, selected.plan
            lines.append(f"Focus stock (the one the user is viewing): {selected.name} ({selected.symbol}), "
                         f"sector {selected.sector or 'n/a'}, rank #{selected.rank or 'n/a'} of {len(ranked)}.")
            if plan:
                lines.append(f"  AiTrading's call on it: {plan.action} ({plan.strategy}), conviction "
                             f"{selected.conviction_score:.0f}/100.")
            lines.append("  Key facts in words: " + "; ".join(self._plain_facts(selected)) + ".")
            lines.append(
                f"  Price {t.close}, change {t.change_pct}% today, trend {t.trend}, RSI {t.rsi and round(t.rsi, 1)}, "
                f"EMA20 {t.ema_20 and round(t.ema_20, 2)}, EMA50 {t.ema_50 and round(t.ema_50, 2)}, "
                f"EMA200 {t.ema_200 and round(t.ema_200, 2)}, 52-week high {t.high_52w and round(t.high_52w, 2)}, "
                f"20-day return {t.return_20d}%, vs NIFTY {t.relative_strength_20d}%, "
                f"technical score {t.technical_score}/100, conviction {selected.conviction_score:.0f}/100."
            )
            if plan:
                lines.append(
                    f"  Plan: {plan.action} ({plan.strategy}), entry {plan.entry_low:.2f}-{plan.entry_high:.2f}, "
                    f"stop {plan.stop_loss:.2f}, target {plan.target:.2f}, 1:{plan.risk_reward:.1f}, "
                    f"{plan.holding_period}, {plan.risk_level} risk. {plan.invalidation}"
                )
            lines.append(f"  Assessment: {ranking_service.build_reasoning(selected)}")
            headlines = selected.sentiment.get("matched_headlines") or []
            lines.append("  Its news: " + (" | ".join(headlines[:3]) if headlines else "no stock-specific headlines"))

        if ranked:
            lines.append("Background — live ranking (best first):" if selected else "Live ranking (best first):")
            for item in ranked[: 5 if selected else 8]:
                plan = item.plan
                levels = (f", entry {plan.entry_low:.2f}-{plan.entry_high:.2f}, stop {plan.stop_loss:.2f}, "
                          f"target {plan.target:.2f}, {plan.holding_period}, {plan.risk_level} risk") if plan else ""
                drivers = "; ".join(ranking_service.score_drivers(item)[:3])
                lines.append(f"#{item.rank} {item.name} ({item.symbol}, {item.sector}): {item.action} "
                             f"{item.strategy if item.strategy != NO_SETUP else ''} conviction "
                             f"{item.conviction_score:.0f}{levels}. Drivers: {drivers or 'n/a'}")

        changes = ranking_service.get_recent_changes(3 if selected else 6)
        if changes:
            lines.append("Recent rank changes: " + " | ".join(
                f"{c['timestamp'][11:16]} UTC {c['symbol']}: {c['reason']}" for c in changes))

        from ai_analyst_service import ai_analyst_service
        from position_service import position_service

        lines.extend(position_service.chat_lines(db))
        # How this user actually trades (measured from their recorded trades) — tailor advice to it.
        from trader_profile_service import trader_profile_service
        style = trader_profile_service.block(db, selected.symbol if selected is not None else None)
        lines.append("The user's trading style, measured from their recorded trades (tailor advice to it; "
                     "don't let it change what the data says): " + style.replace("\n", " | "))
        if selected is not None:
            prediction = ai_analyst_service.todays_predictions(db).get(selected.symbol)
            if prediction is not None:
                lines.append(f"  Your own {prediction.horizon_days}-day forecast on it ({prediction.prediction_date:%d %b}): "
                             f"{prediction.direction}, {float(prediction.probability_up):.0f}% chance higher — "
                             f"{prediction.reason}")
        outlook = ai_analyst_service.outlook_payload(ai_analyst_service.latest_outlook(db))
        if outlook:
            lines.append(f"Your next-session outlook for {outlook['session_date']}: {outlook['bias']} "
                         f"({outlook['probability_up']:.0f}% chance NIFTY closes higher). {outlook['summary'] or ''} "
                         "Sector impacts: " + ("; ".join(f"{s['sector']} {s['impact']:+d} ({s['reason']})"
                                                          for s in outlook["sector_impacts"]) or "none"))
        _, lessons = ai_analyst_service.current_lessons(db)
        if lessons:
            lines.append("Lessons you have learned from your graded forecasts: " + " | ".join(lessons[:6]))
        lines.append("Your forecasting record: " + ai_analyst_service.track_record_text(db))
        lines.append("Strategy track record: " + learning_service.performance_summary(db))
        recent_trades = (
            db.query(TradeHistory).filter(TradeHistory.source == "zerodha")
            .order_by(TradeHistory.execution_date.desc()).limit(5).all()
        )
        if recent_trades:
            lines.append("User's recent trades: " + " | ".join(
                f"{tr.stock.symbol if tr.stock else tr.stock_id} {tr.execution_date:%d %b} "
                f"@{float(tr.entry_price):.2f}"
                + (f"→{float(tr.exit_price):.2f} P&L {float(tr.profit_loss):+.0f}" if tr.exit_price is not None else " open")
                for tr in recent_trades))
        return lines

    @staticmethod
    def _plain_facts(item: RankedStock) -> list[str]:
        """Relationships spelled out, so the model doesn't have to compare numbers itself."""
        t = item.technical
        facts: list[str] = []
        if t.close is None:
            return ["no price data"]
        for label, level in (("EMA 20", t.ema_20), ("EMA 50", t.ema_50), ("EMA 200", t.ema_200)):
            if level:
                facts.append(f"price {t.close:.2f} is {'ABOVE' if t.close > level else 'BELOW'} {label} ({level:.2f})")
        if t.rsi is not None:
            zone = "overbought" if t.rsi > 70 else "weak" if t.rsi < 40 else "neutral-to-strong"
            facts.append(f"RSI {t.rsi:.0f} ({zone})")
        if t.relative_strength_20d is not None:
            facts.append(f"{'outperforming' if t.relative_strength_20d >= 0 else 'underperforming'} NIFTY by "
                         f"{abs(t.relative_strength_20d):.1f}% over 20 days")
        if t.volume_ratio is not None:
            facts.append(f"volume {t.volume_ratio:.1f}x its 20-day average")
        if t.high_52w:
            facts.append(f"{(t.close / t.high_52w - 1) * 100:+.1f}% from the 52-week high")
        facts.append(f"trend {t.trend}")
        return facts

    @staticmethod
    def find_symbol_mentions(db: Session, message: str, tracked_only: bool = True) -> list[str]:
        """Watchlist symbols/names mentioned in a chat message (for 'compare X with Y' questions)."""
        text = message.lower()
        found = []
        query = db.query(Stock).filter(tracked_stock_filter()) if tracked_only else db.query(Stock)
        for stock in query.all():
            names = {stock.symbol.lower(), stock.name.lower()} | {
                k.strip().lower() for k in (stock.keywords or "").split(",") if len(k.strip()) > 3}
            if any(n and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text) for n in names):
                found.append(stock.symbol)
        return found[:4]


insight_service = InsightService()
