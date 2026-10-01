"""
Learning engine (PRD §14-16): improves from evidence, never by rewriting itself.

- grade_matured_recommendations: replays each BUY recommendation against the
  price action after it was issued (did the entry zone fill? stop or target
  first? expired?) and records the outcome in trade_history.
- import_tradebook: parses a Zerodha tradebook upload, FIFO-matches buys and
  sells into round-trip trades (P&L x quantity), de-duplicates re-uploads,
  and links each trade to the recommendation that preceded it.
- strategy_performance: win rate / average return per strategy from all of
  the above. The ranking engine feeds this back in as a bounded "learned
  edge" adjustment, and recalibrate_weights re-balances the score blend.
"""
import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from analysis_service import ALL_STRATEGIES, NO_SETUP
from data_provider import completed_daily_bars, reference_price
from market_service import market_service
from memory_service import memory_service
from models import Recommendation, Stock, TradeHistory
from utils.logger import get_logger
from utils.validators import validate_symbol

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
HOLDING_PERIOD_DAYS_FALLBACK = 10
GRADING_LOOKBACK_LIMIT_DAYS = 90
LINK_WINDOW_DAYS = 10  # a trade within this many days after a recommendation is linked to it
UNLINKED = "Your own trades (unlinked)"
# Trades the user actually made: imported from Zerodha or recorded as holdings in the app.
REAL_SOURCES = {"zerodha", "manual"}


def parse_holding_days(holding_period: str) -> int:
    """Max trading days in "3-8 trading days" / "1-3 weeks" style text."""
    numbers = [int(n) for n in re.findall(r"\d+", holding_period or "")]
    if not numbers:
        return HOLDING_PERIOD_DAYS_FALLBACK
    days = max(numbers)
    return days * 5 if "week" in (holding_period or "").lower() else days


def _to_ist_date(ts: datetime) -> date:
    """Recommendation timestamps are naive UTC."""
    return ts.replace(tzinfo=timezone.utc).astimezone(IST).date()


def grade_against_candles(rec: Recommendation, candles: pd.DataFrame) -> Optional[dict]:
    """
    Replay a BUY recommendation on the bars after it was issued. Returns None
    while it's unresolved and its holding window hasn't finished yet. Only
    finished sessions count, and the plan's levels are rescaled if a split or
    bonus happened after it was issued; the exit price is reported back on the
    recommendation's own scale so it compares with its entry.
    """
    candles = completed_daily_bars(candles)
    issued = _to_ist_date(rec.timestamp)
    _, scale = reference_price(candles, issued, float(rec.entry_price))
    entry_low = float(rec.entry_low if rec.entry_low is not None else rec.entry_price) * scale
    entry_high = float(rec.entry_high if rec.entry_high is not None else rec.entry_price) * scale
    entry = float(rec.entry_price) * scale
    stop, target = float(rec.stop_loss) * scale, float(rec.target_price) * scale
    max_bars = parse_holding_days(rec.holding_period)
    result = _replay(candles, issued, entry_low, entry_high, entry, stop, target, max_bars)
    if result and result["exit_price"] is not None:
        result["exit_price"] = round(result["exit_price"] / scale, 2)
    return result


def _replay(candles: pd.DataFrame, issued: date, entry_low: float, entry_high: float, entry: float,
            stop: float, target: float, max_bars: int) -> Optional[dict]:
    dates = pd.to_datetime(candles["date"])
    if dates.dt.tz is not None:
        dates = dates.dt.tz_convert("Asia/Kolkata")
    bars = candles[dates.dt.date > issued].reset_index(drop=True)
    bar_dates = [d.date() for d in pd.to_datetime(bars["date"])] if not bars.empty else []

    filled_at = None
    for i in range(min(len(bars), max_bars)):
        bar = bars.iloc[i]
        if filled_at is None:
            if float(bar["low"]) <= entry_high and float(bar["high"]) >= entry_low:
                filled_at = i
                # Conservative: a bar that fills and hits the stop counts as stopped.
                if float(bar["low"]) <= stop:
                    return {"outcome": "stop_hit", "exit_price": stop, "exit_date": bar_dates[i]}
            continue
        if float(bar["low"]) <= stop:
            return {"outcome": "stop_hit", "exit_price": stop, "exit_date": bar_dates[i]}
        if float(bar["high"]) >= target:
            return {"outcome": "target_hit", "exit_price": target, "exit_date": bar_dates[i]}

    if len(bars) < max_bars:
        return None  # window still open
    if filled_at is None:
        return {"outcome": "not_triggered", "exit_price": None, "exit_date": bar_dates[max_bars - 1]}
    last = bars.iloc[max_bars - 1]
    exit_price = float(last["close"])
    return {"outcome": "expired_profit" if exit_price > entry else "expired_loss",
            "exit_price": exit_price, "exit_date": bar_dates[max_bars - 1]}


class LearningService:
    # ------------------------------------------------------------------
    # Auto-grading of recommendations
    # ------------------------------------------------------------------
    async def grade_matured_recommendations(self, db: Session) -> int:
        cutoff = datetime.utcnow() - timedelta(days=GRADING_LOOKBACK_LIMIT_DAYS)
        graded_ids = {
            row[0] for row in db.query(TradeHistory.recommendation_id)
            .filter(TradeHistory.recommendation_id.isnot(None), TradeHistory.source == "auto").all()
        }
        candidates = (
            db.query(Recommendation)
            .filter(Recommendation.timestamp >= cutoff)
            .filter(Recommendation.timestamp <= datetime.utcnow() - timedelta(hours=12))
            .all()
        )

        graded = 0
        for rec in candidates:
            if rec.id in graded_ids or rec.action not in (None, "BUY"):
                continue
            stock = db.get(Stock, rec.stock_id)
            if stock is None:
                continue
            days = (datetime.utcnow() - rec.timestamp).days + 10
            candles = await market_service.get_candles_for_symbol(stock.symbol, days=days)
            if candles.empty or "date" not in candles.columns:
                continue
            result = grade_against_candles(rec, candles)
            if result is None:
                continue

            exit_price = result["exit_price"]
            profit_loss = round(exit_price - float(rec.entry_price), 2) if exit_price is not None else None
            db.add(TradeHistory(
                stock_id=rec.stock_id, recommendation_id=rec.id, source="auto",
                execution_date=_to_ist_date(rec.timestamp), exit_date=result["exit_date"],
                entry_price=rec.entry_price, exit_price=exit_price, profit_loss=profit_loss,
                predicted_target=rec.target_price, actual_outcome=result["outcome"],
                timestamp=datetime.utcnow(),
            ))
            db.commit()
            graded += 1

            if result["outcome"] != "not_triggered":
                await memory_service.store_trade_memory(
                    db,
                    setup_description=(
                        f"{stock.symbol} {rec.strategy or 'setup'} technical={rec.technical_score} "
                        f"sentiment={rec.sentiment_score} volume={rec.volume_score} rr={rec.risk_reward}"
                    ),
                    outcome=result["outcome"],
                    accuracy_score=100.0 if result["outcome"] == "target_hit"
                    else 0.0 if result["outcome"] == "stop_hit" else 50.0,
                )
        if graded:
            logger.info("Auto-graded %d matured recommendation(s)", graded)
        return graded

    # ------------------------------------------------------------------
    # Zerodha tradebook import
    # ------------------------------------------------------------------
    def import_tradebook(self, db: Session, df: pd.DataFrame) -> dict:
        """
        FIFO-match BUY and SELL executions into round trips. Trades still open
        from earlier uploads are carried in as lots, so a later upload that
        only contains the SELL still closes them. Every stored row has a
        trade_ref, so uploading the same file twice imports nothing new.
        """
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        sort_cols = ["order_execution_time"] if "order_execution_time" in df.columns else ["trade_date"]
        if sort_cols[0] == "order_execution_time":
            df["order_execution_time"] = pd.to_datetime(df["order_execution_time"], errors="coerce")
            df["order_execution_time"] = df["order_execution_time"].fillna(df["trade_date"])
        df = df.sort_values(sort_cols, kind="stable").reset_index(drop=True)

        def row_ref(row) -> str:
            if "trade_id" in row and pd.notna(row["trade_id"]) and str(row["trade_id"]).strip():
                return str(row["trade_id"]).strip().removesuffix(".0")
            raw = f"{row['symbol']}|{row['trade_date']}|{row['trade_type']}|{row['quantity']}|{row['price']}"
            return "h" + hashlib.sha1(raw.encode()).hexdigest()[:16]

        symbols = {str(s).strip().upper() for s in df["symbol"]}
        stock_cache: dict[str, Stock] = {}

        def get_stock(symbol: str) -> Stock:
            stock = stock_cache.get(symbol)
            if stock is None:
                stock = db.query(Stock).filter(Stock.symbol == symbol).first()
                if stock is None:
                    stock = Stock(symbol=symbol, name=symbol, watchlist_status="inactive")
                    db.add(stock)
                    db.flush()
                stock_cache[symbol] = stock
            return stock

        # Existing imported trades: known execution refs, and open lots to carry forward.
        known_refs: set[str] = set()
        open_lots: dict[str, list[dict]] = defaultdict(list)
        existing = (
            db.query(TradeHistory).join(Stock)
            .filter(TradeHistory.source == "zerodha", Stock.symbol.in_(symbols)).all()
        )
        for row in existing:
            for part in re.split(r"[>:]", row.trade_ref or ""):
                if part and part != "open":
                    known_refs.add(part)
            if row.exit_price is None and row.trade_ref and row.trade_ref.startswith("open:"):
                open_lots[row.stock.symbol].append({
                    "quantity": float(row.quantity or 0), "price": float(row.entry_price),
                    "date": row.execution_date, "ref": row.trade_ref.split(":", 1)[1],
                })
                db.delete(row)
        db.flush()

        imported, skipped, errors = 0, 0, []
        closed_rows: list[TradeHistory] = []
        for idx, row in df.iterrows():
            try:
                symbol = validate_symbol(str(row["symbol"]))
                trade_type = str(row["trade_type"]).strip().upper()
                price, quantity = float(row["price"]), float(row["quantity"])
                trade_date = row["trade_date"].date()
                ref = row_ref(row)
                if ref in known_refs:
                    skipped += 1
                    continue
                known_refs.add(ref)
                lots = open_lots[symbol]

                if trade_type == "BUY":
                    lots.append({"quantity": quantity, "price": price, "date": trade_date, "ref": ref})
                elif trade_type == "SELL":
                    remaining = quantity
                    while remaining > 0 and lots:
                        lot = lots[0]
                        matched = min(lot["quantity"], remaining)
                        pnl = round((price - lot["price"]) * matched, 2)
                        trade = TradeHistory(
                            stock_id=get_stock(symbol).id, source="zerodha",
                            trade_ref=f"{lot['ref']}>{ref}"[:200],
                            execution_date=lot["date"], exit_date=trade_date,
                            entry_price=lot["price"], exit_price=price, quantity=matched,
                            profit_loss=pnl,
                            actual_outcome="profit" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
                            timestamp=datetime.utcnow(),
                        )
                        db.add(trade)
                        closed_rows.append(trade)
                        imported += 1
                        lot["quantity"] -= matched
                        remaining -= matched
                        if lot["quantity"] <= 1e-9:
                            lots.pop(0)
                    if remaining > 0:
                        errors.append(f"row {idx}: SELL of {remaining:g} {symbol} has no matching BUY on record "
                                      f"(short sales and pre-upload holdings aren't matched)")
                else:
                    errors.append(f"row {idx}: unrecognized trade_type {row['trade_type']!r} (expected BUY or SELL)")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"row {idx}: {getattr(exc, 'detail', exc)}")

        open_positions = 0
        for symbol, lots in open_lots.items():
            for lot in lots:
                if lot["quantity"] <= 1e-9:
                    continue
                db.add(TradeHistory(
                    stock_id=get_stock(symbol).id, source="zerodha", trade_ref=f"open:{lot['ref']}"[:200],
                    execution_date=lot["date"], entry_price=lot["price"], quantity=lot["quantity"],
                    exit_price=None, profit_loss=None, actual_outcome=None, timestamp=datetime.utcnow(),
                ))
                open_positions += 1

        db.flush()
        linked = self._link_to_recommendations(db, closed_rows)
        db.commit()
        logger.info("Tradebook import: %d closed trades, %d open positions, %d linked, %d duplicates skipped",
                    imported, open_positions, linked, skipped)
        return {"rows_processed": len(df), "rows_imported": imported, "open_positions": open_positions,
                "linked_to_recommendations": linked, "duplicates_skipped": skipped, "errors": errors}

    @staticmethod
    def _link_to_recommendations(db: Session, trades: list[TradeHistory]) -> int:
        """Link each imported trade to the latest recommendation issued in the days before its entry."""
        linked = 0
        for trade in trades:
            window_start = datetime.combine(trade.execution_date - timedelta(days=LINK_WINDOW_DAYS), datetime.min.time())
            window_end = datetime.combine(trade.execution_date, datetime.max.time())
            rec = (
                db.query(Recommendation)
                .filter(Recommendation.stock_id == trade.stock_id,
                        Recommendation.timestamp >= window_start,
                        Recommendation.timestamp <= window_end)
                .order_by(Recommendation.timestamp.desc())
                .first()
            )
            if rec is not None:
                trade.recommendation_id = rec.id
                trade.predicted_target = rec.target_price
                linked += 1
        return linked

    # ------------------------------------------------------------------
    # Strategy library performance
    # ------------------------------------------------------------------
    def strategy_performance(self, db: Session) -> list[dict]:
        """
        Win rate and average return per strategy. A recommendation counts once:
        a real Zerodha trade linked to it replaces its auto-graded outcome.
        Unlinked real trades are reported as their own row.
        """
        rows = (
            db.query(TradeHistory, Recommendation)
            .outerjoin(Recommendation, TradeHistory.recommendation_id == Recommendation.id)
            .filter(TradeHistory.actual_outcome.isnot(None), TradeHistory.exit_price.isnot(None))
            .all()
        )
        by_rec: dict[int, tuple] = {}
        unlinked: list[tuple] = []
        for trade, rec in rows:
            if rec is None:
                if trade.source in REAL_SOURCES:
                    unlinked.append((trade, None))
                continue
            current = by_rec.get(rec.id)
            if current is None or (trade.source in REAL_SOURCES and current[0].source not in REAL_SOURCES):
                by_rec[rec.id] = (trade, rec)

        buckets: dict[str, list[tuple]] = defaultdict(list)
        for trade, rec in by_rec.values():
            buckets[rec.strategy or NO_SETUP].append((trade, rec))
        if unlinked:
            buckets[UNLINKED] = unlinked

        results = []
        for strategy in ALL_STRATEGIES + [NO_SETUP, UNLINKED]:
            items = buckets.get(strategy, [])
            if not items and strategy in (NO_SETUP, UNLINKED):
                continue
            wins = sum(1 for t, _ in items if float(t.exit_price) > float(t.entry_price))
            returns = [(float(t.exit_price) / float(t.entry_price) - 1) * 100 for t, _ in items]
            results.append({
                "strategy": strategy,
                "trades": len(items),
                "wins": wins,
                "losses": len(items) - wins,
                "win_rate": round(wins / len(items) * 100, 1) if items else None,
                "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
                "real_trades": sum(1 for t, _ in items if t.source in REAL_SOURCES),
            })
        return results

    def performance_summary(self, db: Session) -> str:
        """One-paragraph summary for chat context."""
        stats = [s for s in self.strategy_performance(db) if s["trades"]]
        if not stats:
            return "No graded recommendations or uploaded trades yet."
        return "; ".join(
            f"{s['strategy']}: {s['trades']} trades, {s['win_rate']:.0f}% wins, avg {s['avg_return_pct']:+.1f}%"
            for s in stats
        )

    # ------------------------------------------------------------------
    # Full cycle
    # ------------------------------------------------------------------
    async def run_learning_cycle(self, db: Session) -> dict:
        from ranking_service import ranking_service  # local import: ranking imports us

        from ai_analyst_service import ai_analyst_service

        graded = await self.grade_matured_recommendations(db)
        recalibration = ranking_service.recalibrate_weights(db)
        from position_service import position_service

        ai_graded = await ai_analyst_service.grade_predictions(db)
        await ai_analyst_service.grade_outlooks(db)
        await position_service.grade_alerts(db)
        return {"graded": graded, "ai_forecasts_graded": ai_graded, "recalibration": recalibration,
                "strategies": self.strategy_performance(db)}


learning_service = LearningService()
