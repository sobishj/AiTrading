"""
Holdings: the shares you actually bought, watched continuously.

- Record buys (averaging in), partial or full sells, and your own stop-loss /
  target (defaulting to the AI plan).
- monitor(): for every open holding, each minute in market hours (every 30
  minutes otherwise), check the live price and the engine's view and raise
  alerts:
    stop_hit / near_stop       - price at or just above your stop-loss
    target_hit / near_target   - price at or just below your target
    trail_stop                 - up 1R+: suggest moving the stop up to protect gains
    loss_risk                  - a 0-100 "risk of loss" score crosses the threshold,
                                 with the reasons (trend break, heavy selling,
                                 negative AI news read, bearish AI forecast,
                                 negative sector outlook, risk-off market...)
  Alerts are de-duplicated, pushed to the app, and shown as Windows
  notifications.
- Learning: every sell becomes a real trade in trade_history (quantity and
  rupee P&L, linked to the recommendation it followed), feeding strategy
  statistics and weight recalibration. Risk warnings are graded 5 trading days
  later (did the price actually fall?), and the loss-risk threshold adapts to
  that measured reliability.
"""
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from data_provider import completed_daily_bars, reference_price, yahoo_provider
from market_service import market_service
from models import AppSettings, Position, PositionAlert, PositionTransaction, Recommendation, Stock, TradeHistory
from utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

BASE_RISK_THRESHOLD = 60.0
RISK_THRESHOLD_MIN, RISK_THRESHOLD_MAX = 50.0, 80.0
MIN_GRADED_TO_ADAPT = 20
GRADE_AFTER_BARS = 5
NEAR_STOP_MIN_PCT = 1.0          # "near stop" band: at least 1% above the stop (or half an ATR)
NEAR_TARGET_PCT = 1.0
LINK_WINDOW_DAYS = 10

# Minimum gap before the same kind of alert repeats for a holding.
COOLDOWN = {
    "stop_hit": timedelta(hours=12), "target_hit": timedelta(hours=12),
    "near_stop": timedelta(hours=6), "near_target": timedelta(hours=6),
    "loss_risk": timedelta(hours=6), "trail_stop": timedelta(days=2),
}
DESKTOP_KINDS = {"stop_hit", "near_stop", "target_hit", "loss_risk"}
GRADED_KINDS = {"loss_risk", "near_stop"}


@dataclass
class Evaluation:
    price: float
    pnl: float
    pnl_pct: float
    r_multiple: Optional[float]
    loss_risk: float
    reasons: list[str]
    alerts: list[dict] = field(default_factory=list)   # {kind, severity, title, message}


def evaluate(*, name: str, quantity: float, avg_price: float, price: float,
             stop_loss: Optional[float], target: Optional[float], technical=None, plan_action: Optional[str] = None,
             ai_forecast: Optional[tuple[float, str]] = None, ai_news_impacts: Optional[list[int]] = None,
             sector_impact: Optional[int] = None, market_regime: str = "neutral",
             risk_threshold: float = BASE_RISK_THRESHOLD) -> Evaluation:
    """Pure assessment of one holding (no I/O), so the rules are unit-testable."""
    pnl = (price - avg_price) * quantity
    pnl_pct = (price / avg_price - 1) * 100
    r_multiple = None
    if stop_loss and stop_loss < avg_price:
        r_multiple = (price - avg_price) / (avg_price - stop_loss)

    # ---- risk-of-loss score (0-100) and its reasons
    score, reasons = 0.0, []

    def add(points: float, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    t = technical
    if t is not None and getattr(t, "close", None):
        if t.trend == "downtrend":
            add(25, "Price is in a downtrend (below EMA 20 and EMA 50)")
        else:
            if t.ema_20 and price < t.ema_20:
                add(10, f"Price fell below EMA 20 ({t.ema_20:,.2f})")
            if t.ema_50 and price < t.ema_50:
                add(15, f"Price fell below EMA 50 ({t.ema_50:,.2f})")
        if t.macd is not None and t.macd_signal is not None and t.macd < t.macd_signal:
            add(10, "MACD crossed below its signal line (momentum fading)")
        if t.volume_ratio and t.volume_ratio >= 1.5 and (t.change_pct or 0) < 0:
            add(15, f"Heavy selling: {t.volume_ratio:.1f}x normal volume on a down day")
    if plan_action == "AVOID":
        add(15, "The engine rates this stock AVOID now")
    if ai_news_impacts:
        worst = min(ai_news_impacts)
        if worst <= -2:
            add(20, "Strongly negative news (AI read)")
        elif worst <= -1:
            add(10, "Negative news (AI read)")
    if ai_forecast:
        probability_up, direction = ai_forecast
        if direction == "down" or probability_up < 35:
            add(15, f"AI analyst forecasts a fall ({probability_up:.0f}% chance of rising)")
        elif probability_up < 45:
            add(8, f"AI analyst leans bearish ({probability_up:.0f}% chance of rising)")
    if sector_impact is not None and sector_impact < 0:
        add(5 * abs(sector_impact), "Next-session outlook is negative for its sector")
    if market_regime == "risk-off":
        add(5, "Market regime is risk-off")
    if pnl_pct <= -0.5:
        add(5, f"Trading below your buy price ({pnl_pct:+.1f}%)")
    score = min(100.0, score)

    ev = Evaluation(price=price, pnl=pnl, pnl_pct=pnl_pct, r_multiple=r_multiple, loss_risk=round(score, 1),
                    reasons=reasons)

    def alert(kind: str, severity: str, title: str, message: str) -> None:
        ev.alerts.append({"kind": kind, "severity": severity, "title": title, "message": message})

    money = f"{'+' if pnl >= 0 else '−'}₹{abs(pnl):,.0f} ({pnl_pct:+.1f}%)"
    atr_pct = (getattr(t, "atr_pct", None) or 0) if t is not None else 0

    # ---- stop-loss
    if stop_loss:
        if price <= stop_loss:
            alert("stop_hit", "critical", f"{name}: stop-loss hit — SELL suggested",
                  f"{name} is at ₹{price:,.2f}, at or below your stop-loss ₹{stop_loss:,.2f}. "
                  f"Position {money}. Suggested: sell to cap the loss, then mark it sold in AiTrading.")
        elif price <= stop_loss * (1 + max(NEAR_STOP_MIN_PCT, atr_pct / 2) / 100):
            alert("near_stop", "warning", f"{name}: close to stop-loss",
                  f"{name} is at ₹{price:,.2f}, within {((price / stop_loss) - 1) * 100:.1f}% of your stop "
                  f"₹{stop_loss:,.2f}. Position {money}.")
    # ---- target
    if target:
        if price >= target:
            trail = max(avg_price, stop_loss or 0, round(price * 0.97, 2))
            alert("target_hit", "critical", f"{name}: target reached — SELL suggested",
                  f"{name} hit ₹{price:,.2f}, at or above your target ₹{target:,.2f}. Position {money}. "
                  f"Suggested: sell to book the profit (or raise the stop to ₹{trail:,.2f} to let it run), "
                  f"then mark it sold in AiTrading.")
        elif price >= target * (1 - NEAR_TARGET_PCT / 100):
            alert("near_target", "info", f"{name}: near target",
                  f"{name} is at ₹{price:,.2f}, within {((target / price) - 1) * 100:.1f}% of your target ₹{target:,.2f}.")
    # ---- protect gains
    if r_multiple is not None and r_multiple >= 1 and stop_loss is not None:
        risk_per_share = avg_price - stop_loss
        suggested = avg_price + risk_per_share if r_multiple >= 2 else avg_price
        if stop_loss < suggested - 0.01:
            alert("trail_stop", "info", f"{name}: protect your gain",
                  f"Up {r_multiple:.1f}R ({money}). Consider moving your stop from ₹{stop_loss:,.2f} "
                  f"to ₹{suggested:,.2f}{' (break-even)' if suggested == avg_price else ' (locks in 1R)'}.")
    # ---- loss risk
    if score >= risk_threshold and not any(a["kind"] == "stop_hit" for a in ev.alerts):
        alert("loss_risk", "warning", f"{name}: risk of loss rising ({score:.0f}/100)",
              f"{name} at ₹{price:,.2f}, position {money}. " + "; ".join(reasons[:4]) + ".")
    return ev


def round_tick(price: float) -> float:
    return round(round(price / 0.05) * 0.05, 2)


@dataclass
class Replay:
    quantity: float
    avg_price: float
    realized_pnl: float
    sell_pnls: dict            # transaction id -> realised P&L of that sale
    sell_costs: dict           # transaction id -> average cost it was sold against


def replay(transactions: list) -> Replay:
    """
    Rebuild a holding from its transactions in order (average-cost method), so
    editing or deleting any buy/sell keeps quantity, cost and P&L consistent.
    Raises ValueError if a sale would exceed the shares held at that point.
    """
    quantity, avg, realized = 0.0, 0.0, 0.0
    sell_pnls, sell_costs = {}, {}
    for tx in transactions:
        qty, price = float(tx.quantity), float(tx.price)
        if tx.side == "BUY":
            avg = (quantity * avg + qty * price) / (quantity + qty)
            quantity += qty
        else:
            if qty > quantity + 1e-9:
                raise ValueError(f"A sale of {qty:g} shares on {tx.trade_date} is more than the {quantity:g} held then.")
            pnl = round((price - avg) * qty, 2)
            sell_pnls[tx.id], sell_costs[tx.id] = pnl, avg
            realized += pnl
            quantity -= qty
    return Replay(quantity=0.0 if quantity <= 1e-9 else quantity, avg_price=round(avg, 4),
                  realized_pnl=round(realized, 2), sell_pnls=sell_pnls, sell_costs=sell_costs)


def suggest(*, avg_price: float, price: float, stop_loss: Optional[float], target: Optional[float],
            plan_stop: Optional[float], plan_target: Optional[float], atr: Optional[float],
            loss_risk: float, risk_threshold: float, plan_action: Optional[str]) -> dict:
    """
    The AI's advice for a holding (pure): SELL / CONSIDER_SELLING / HOLD, plus the
    stop-loss and target (sell value) it would use now. The suggested stop only
    ever tightens as the trade works: break-even after a 1R gain, +1R after 2R.
    """
    atr = atr or price * 0.02
    base_stop = plan_stop if plan_stop and plan_stop < price else price - 1.5 * atr
    initial_risk = (avg_price - stop_loss) if stop_loss and stop_loss < avg_price else (avg_price - base_stop)
    r_multiple = (price - avg_price) / initial_risk if initial_risk > 0 else None
    suggested_stop = base_stop
    if r_multiple is not None and r_multiple >= 2:
        suggested_stop = max(suggested_stop, avg_price + initial_risk)
    elif r_multiple is not None and r_multiple >= 1:
        suggested_stop = max(suggested_stop, avg_price)
    if stop_loss:
        suggested_stop = max(suggested_stop, stop_loss) if stop_loss < price else suggested_stop
    suggested_stop = round_tick(min(suggested_stop, price * 0.995))

    suggested_target = plan_target if plan_target and plan_target > price * 1.005 else price + 2 * (price - suggested_stop)
    suggested_target = round_tick(suggested_target)

    in_loss = price < avg_price
    if stop_loss and price <= stop_loss:
        action, reason = "SELL", f"Price ₹{price:,.2f} is at or below your stop-loss ₹{stop_loss:,.2f} — exit to cap the loss."
    elif target and price >= target:
        action, reason = ("SELL", f"Target ₹{target:,.2f} reached — book the profit, or keep holding with the stop "
                                  f"raised to ₹{suggested_stop:,.2f}.")
    elif loss_risk >= max(risk_threshold, 75) and in_loss:
        action, reason = "CONSIDER_SELLING", f"Risk of a bigger loss is high ({loss_risk:.0f}/100) and you're below your buy price."
    elif plan_action == "AVOID" and in_loss and loss_risk >= risk_threshold:
        action, reason = "CONSIDER_SELLING", "The engine now rates it AVOID and the loss risk is elevated."
    elif loss_risk >= risk_threshold:
        action, reason = "HOLD", f"Hold, but watch closely — loss risk {loss_risk:.0f}/100. Keep the stop at ₹{suggested_stop:,.2f}."
    else:
        action, reason = "HOLD", "Hold — the setup is intact."
    return {"action": action, "reason": reason, "suggested_stop": suggested_stop,
            "suggested_target": suggested_target,
            "r_multiple": round(r_multiple, 2) if r_multiple is not None else None,
            "plan_action": plan_action}


class PositionService:
    # ------------------------------------------------------------------
    # Recording trades
    # ------------------------------------------------------------------
    @staticmethod
    def _link_recommendation(db: Session, stock_id: int, on: date) -> Optional[int]:
        start = datetime.combine(on - timedelta(days=LINK_WINDOW_DAYS), datetime.min.time())
        rec = (db.query(Recommendation)
               .filter(Recommendation.stock_id == stock_id, Recommendation.timestamp >= start,
                       Recommendation.timestamp <= datetime.combine(on, datetime.max.time()))
               .order_by(Recommendation.timestamp.desc()).first())
        return rec.id if rec else None

    def buy(self, db: Session, stock: Stock, quantity: float, price: float, trade_date: date,
            stop_loss: Optional[float], target: Optional[float], notes: Optional[str]) -> Position:
        """Open a holding, or add to the open one for this stock (averaging the cost)."""
        position = (db.query(Position)
                    .filter(Position.stock_id == stock.id, Position.status == "open").first())
        if position is None:
            position = Position(stock_id=stock.id, status="open", quantity=quantity, avg_price=price,
                                opened_on=trade_date, stop_loss=stop_loss, target=target, notes=notes,
                                recommendation_id=self._link_recommendation(db, stock.id, trade_date),
                                realized_pnl=0)
            db.add(position)
            db.flush()
        else:
            if stop_loss is not None:
                position.stop_loss = stop_loss
            if target is not None:
                position.target = target
            if notes:
                position.notes = notes
        db.add(PositionTransaction(position_id=position.id, side="BUY", quantity=quantity, price=price,
                                   trade_date=trade_date))
        db.flush()
        self.rebuild(db, position)
        db.commit()
        db.refresh(position)
        return position

    def sell(self, db: Session, position: Position, quantity: float, price: float, trade_date: date) -> PositionTransaction:
        """Record a partial or full sale; the realised trade is added to trade_history for learning."""
        held = float(position.quantity)
        if quantity > held + 1e-9:
            raise ValueError(f"You hold {held:g} shares; can't sell {quantity:g}.")
        tx = PositionTransaction(position_id=position.id, side="SELL", quantity=quantity, price=price,
                                 trade_date=trade_date)
        db.add(tx)
        db.flush()
        db.refresh(position)
        self.rebuild(db, position)
        db.commit()
        db.refresh(tx)
        return tx

    def rebuild(self, db: Session, position: Position) -> None:
        """
        Recompute the holding from its transactions and re-create the learning
        rows for its sales (one trade_history row per sale, quantity x P&L).
        """
        transactions = sorted(position.transactions, key=lambda t: (t.trade_date, t.id))
        result = replay(transactions)          # raises ValueError on an impossible sale
        buys = [t for t in transactions if t.side == "BUY"]
        position.quantity = result.quantity
        position.avg_price = result.avg_price if result.quantity else position.avg_price or result.avg_price
        position.realized_pnl = result.realized_pnl
        position.opened_on = buys[0].trade_date if buys else position.opened_on
        if result.quantity <= 0 and transactions and transactions[-1].side == "SELL":
            position.status = "closed"
            position.closed_at = position.closed_at or datetime.utcnow()
        else:
            position.status, position.closed_at = "open", None

        db.query(TradeHistory).filter(TradeHistory.trade_ref.like(f"pos:{position.id}:%")).delete(
            synchronize_session=False)
        for tx in transactions:
            if tx.side != "SELL":
                continue
            pnl, cost = result.sell_pnls[tx.id], result.sell_costs[tx.id]
            tx.realized_pnl = pnl
            db.add(TradeHistory(
                stock_id=position.stock_id, recommendation_id=position.recommendation_id, source="manual",
                trade_ref=f"pos:{position.id}:{tx.id}", execution_date=position.opened_on, exit_date=tx.trade_date,
                entry_price=round(cost, 4), exit_price=float(tx.price), quantity=float(tx.quantity), profit_loss=pnl,
                predicted_target=position.target,
                actual_outcome="profit" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
                timestamp=datetime.utcnow(),
            ))

    def edit_transaction(self, db: Session, position: Position, tx_id: int, quantity: float, price: float,
                         trade_date: date) -> None:
        tx = next((t for t in position.transactions if t.id == tx_id), None)
        if tx is None:
            raise LookupError("Transaction not found")
        old = (tx.quantity, tx.price, tx.trade_date)
        tx.quantity, tx.price, tx.trade_date = quantity, price, trade_date
        db.flush()
        try:
            self.rebuild(db, position)
        except ValueError:
            tx.quantity, tx.price, tx.trade_date = old
            db.rollback()
            raise
        db.commit()

    def delete_transaction(self, db: Session, position: Position, tx_id: int) -> bool:
        """Delete one buy or sale (e.g. undo a sale recorded by mistake). Returns False if nothing is left."""
        tx = next((t for t in position.transactions if t.id == tx_id), None)
        if tx is None:
            raise LookupError("Transaction not found")
        remaining = [t for t in position.transactions if t.id != tx_id]
        if not any(t.side == "BUY" for t in remaining):
            self.delete(db, position)
            return False
        replay(sorted(remaining, key=lambda t: (t.trade_date, t.id)))   # validate before changing anything
        position.transactions.remove(tx)
        db.delete(tx)
        db.flush()
        self.rebuild(db, position)
        db.commit()
        return True

    @staticmethod
    def delete(db: Session, position: Position) -> None:
        """Remove a holding entered by mistake, including the learning rows its sells created."""
        db.query(TradeHistory).filter(TradeHistory.trade_ref.like(f"pos:{position.id}:%")).delete(
            synchronize_session=False)
        db.delete(position)
        db.commit()

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------
    def risk_threshold(self, db: Session) -> float:
        """Adapt the loss-risk threshold to how reliable past risk warnings proved to be."""
        graded = (db.query(PositionAlert)
                  .filter(PositionAlert.kind == "loss_risk", PositionAlert.graded_at.isnot(None)).all())
        if len(graded) < MIN_GRADED_TO_ADAPT:
            return BASE_RISK_THRESHOLD
        precision = sum(1 for a in graded if a.correct) / len(graded)
        adjust = 10 if precision < 0.4 else 5 if precision < 0.5 else -5 if precision > 0.7 else 0
        return max(RISK_THRESHOLD_MIN, min(RISK_THRESHOLD_MAX, BASE_RISK_THRESHOLD + adjust))

    async def monitor(self, db: Session) -> list[PositionAlert]:
        """Assess every open holding now; returns the new alerts (already stored)."""
        from ai_analyst_service import ai_analyst_service
        from ranking_service import ranking_service

        positions = db.query(Position).filter(Position.status == "open").all()
        if not positions:
            return []
        threshold = self.risk_threshold(db)
        forecasts = {s: (float(p.probability_up), p.direction)
                     for s, p in ai_analyst_service.todays_predictions(db).items()}
        news = ai_analyst_service.news_sentiment_map(db)
        outlook = ai_analyst_service.outlook_payload(ai_analyst_service.latest_outlook(db)) or {}
        sector_impacts = {i["sector"]: i["impact"] for i in outlook.get("sector_impacts", [])}
        regime = ranking_service.context.market_regime

        created: list[PositionAlert] = []
        for position in positions:
            stock = position.stock
            item = ranking_service.get_cached_ranked_stock(stock.symbol)
            if item is None:
                try:
                    item = await ranking_service.rank_stock(stock)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not assess %s: %s", stock.symbol, exc)
            quote = await yahoo_provider.get_quote(stock.symbol)
            price = (quote or {}).get("price") or (item.technical.close if item else None)
            if not price:
                continue
            ev = evaluate(
                name=stock.name, quantity=float(position.quantity), avg_price=float(position.avg_price),
                price=float(price), stop_loss=float(position.stop_loss) if position.stop_loss is not None else None,
                target=float(position.target) if position.target is not None else None,
                technical=item.technical if item else None, plan_action=item.action if item else None,
                ai_forecast=forecasts.get(stock.symbol),
                ai_news_impacts=[r["impact"] for r in news.get(stock.symbol, {}).get("reads", [])],
                sector_impact=sector_impacts.get(stock.sector), market_regime=regime, risk_threshold=threshold,
            )
            position.last_price = ev.price
            position.loss_risk = ev.loss_risk
            position.risk_reasons = json.dumps(ev.reasons)
            position.suggestion_json = json.dumps(self.suggestion_for(position, item, ev, threshold))
            position.last_checked_at = datetime.utcnow()
            for candidate in ev.alerts:
                if self._should_raise(db, position, candidate, ev.loss_risk):
                    alert = PositionAlert(position_id=position.id, price=ev.price, loss_risk=ev.loss_risk,
                                          **candidate)
                    db.add(alert)
                    created.append(alert)
        db.commit()
        for alert in created:
            db.refresh(alert)
        if created:
            logger.info("Holding alerts: %s", ", ".join(f"{a.position.stock.symbol}:{a.kind}" for a in created))
        return created

    @staticmethod
    def suggestion_for(position: Position, item, ev: "Evaluation", threshold: float) -> dict:
        plan = item.plan if item else None
        t = item.technical if item else None
        return suggest(
            avg_price=float(position.avg_price), price=ev.price,
            stop_loss=float(position.stop_loss) if position.stop_loss is not None else None,
            target=float(position.target) if position.target is not None else None,
            plan_stop=plan.stop_loss if plan else None, plan_target=plan.target if plan else None,
            atr=t.atr if t else None, loss_risk=ev.loss_risk, risk_threshold=threshold,
            plan_action=plan.action if plan else None,
        )

    @staticmethod
    def _should_raise(db: Session, position: Position, candidate: dict, loss_risk: float) -> bool:
        last = (db.query(PositionAlert)
                .filter(PositionAlert.position_id == position.id, PositionAlert.kind == candidate["kind"])
                .order_by(PositionAlert.created_at.desc()).first())
        if last is None:
            return True
        if datetime.utcnow() - last.created_at >= COOLDOWN.get(candidate["kind"], timedelta(hours=6)):
            return True
        # Within the cooldown, only a clearly worse loss-risk reading is worth another alert.
        return candidate["kind"] == "loss_risk" and loss_risk >= float(last.loss_risk or 0) + 15

    async def notify(self, db: Session, alerts: list[PositionAlert]) -> None:
        """Windows notifications for the important alerts (if enabled in settings)."""
        from notifier import notify_desktop

        cfg = db.get(AppSettings, 1)
        if cfg is not None and not cfg.desktop_notifications:
            return
        for alert in alerts:
            if alert.kind in DESKTOP_KINDS:
                await notify_desktop(f"AiTrading — {alert.title}", alert.message,
                                     urgent=alert.severity == "critical")

    # ------------------------------------------------------------------
    # Learning: grade risk warnings
    # ------------------------------------------------------------------
    async def grade_alerts(self, db: Session) -> int:
        cutoff = datetime.utcnow() - timedelta(days=GRADE_AFTER_BARS + 2)
        pending = (db.query(PositionAlert)
                   .filter(PositionAlert.kind.in_(GRADED_KINDS), PositionAlert.graded_at.is_(None),
                           PositionAlert.created_at <= cutoff).all())
        graded = 0
        for alert in pending:
            stock = alert.position.stock
            candles = completed_daily_bars(await market_service.get_candles_for_symbol(stock.symbol, days=40))
            if candles.empty or "date" not in candles.columns:
                continue
            dates = pd.to_datetime(candles["date"])
            if dates.dt.tz is not None:
                dates = dates.dt.tz_convert("Asia/Kolkata")
            alert_day = alert.created_at.replace(tzinfo=timezone.utc).astimezone(IST).date()
            after = candles[dates.dt.date > alert_day].reset_index(drop=True)
            if len(after) < GRADE_AFTER_BARS:
                continue
            window = after.iloc[:GRADE_AFTER_BARS]
            price, _ = reference_price(candles, alert_day, float(alert.price))
            ret = (float(window["close"].iloc[-1]) / price - 1) * 100
            stop = alert.position.stop_loss
            stop_touched = stop is not None and float(window["low"].min()) <= float(stop)
            alert.outcome_return_pct = round(ret, 2)
            alert.correct = bool(ret < -0.5 or stop_touched)   # the warning was right if the price fell
            alert.graded_at = datetime.utcnow()
            graded += 1
        db.commit()
        return graded

    def alert_stats(self, db: Session) -> dict:
        stats = []
        for kind in sorted(GRADED_KINDS):
            graded = (db.query(PositionAlert)
                      .filter(PositionAlert.kind == kind, PositionAlert.graded_at.isnot(None)).all())
            stats.append({"kind": kind, "graded": len(graded),
                          "precision": round(sum(1 for a in graded if a.correct) / len(graded) * 100, 1) if graded else None})
        closed = db.query(Position).filter(Position.status == "closed").all()
        realized = sum(float(p.realized_pnl or 0) for p in db.query(Position).all())
        wins = sum(1 for p in closed if float(p.realized_pnl or 0) > 0)
        return {
            "alerts": stats,
            "risk_threshold": self.risk_threshold(db),
            "base_risk_threshold": BASE_RISK_THRESHOLD,
            "closed_positions": len(closed),
            "closed_win_rate": round(wins / len(closed) * 100, 1) if closed else None,
            "realized_pnl": round(realized, 2),
        }

    # ------------------------------------------------------------------
    # API shapes
    # ------------------------------------------------------------------
    @staticmethod
    def to_payload(position: Position) -> dict:
        price = float(position.last_price) if position.last_price is not None else None
        qty, avg = float(position.quantity), float(position.avg_price)
        unrealized = (price - avg) * qty if price is not None and qty else 0.0
        unread = [a for a in position.alerts if not a.acknowledged]
        sells = [t for t in position.transactions if t.side == "SELL"]
        sold_qty = sum(float(t.quantity) for t in sells)
        proceeds = sum(float(t.quantity) * float(t.price) for t in sells)
        realized = sum(float(t.realized_pnl or 0) for t in sells)
        cost_sold = proceeds - realized
        return {
            "id": position.id,
            "symbol": position.stock.symbol,
            "name": position.stock.name,
            "status": position.status,
            "quantity": qty,
            "avg_price": round(avg, 2),
            "invested": round(qty * avg, 2),
            "opened_on": position.opened_on.isoformat(),
            "stop_loss": float(position.stop_loss) if position.stop_loss is not None else None,
            "target": float(position.target) if position.target is not None else None,
            "notes": position.notes,
            "last_price": price,
            "unrealized_pnl": round(unrealized, 2),
            "unrealized_pct": round((price / avg - 1) * 100, 2) if price else None,
            "realized_pnl": round(float(position.realized_pnl or 0), 2),
            # sold part of the trade (for the Sold view)
            "sold_quantity": sold_qty,
            "avg_sell_price": round(proceeds / sold_qty, 2) if sold_qty else None,
            "avg_buy_price_sold": round(cost_sold / sold_qty, 2) if sold_qty else None,
            "realized_pct": round(realized / cost_sold * 100, 2) if cost_sold else None,
            "last_sold_on": max(t.trade_date for t in sells).isoformat() if sells else None,
            "closed_at": position.closed_at.isoformat() if position.closed_at else None,
            "loss_risk": float(position.loss_risk) if position.loss_risk is not None else None,
            "risk_reasons": json.loads(position.risk_reasons or "[]"),
            "suggestion": json.loads(position.suggestion_json) if position.suggestion_json else None,
            "last_checked_at": position.last_checked_at.isoformat() if position.last_checked_at else None,
            "unread_alerts": len(unread),
            "transactions": [{"id": t.id, "side": t.side, "quantity": float(t.quantity), "price": float(t.price),
                              "date": t.trade_date.isoformat(),
                              "realized_pnl": float(t.realized_pnl) if t.realized_pnl is not None else None}
                             for t in position.transactions],
        }

    @staticmethod
    def alert_payload(alert: PositionAlert) -> dict:
        return {
            "id": alert.id, "position_id": alert.position_id, "symbol": alert.position.stock.symbol,
            "name": alert.position.stock.name, "kind": alert.kind, "severity": alert.severity,
            "title": alert.title, "message": alert.message, "price": float(alert.price),
            "loss_risk": float(alert.loss_risk) if alert.loss_risk is not None else None,
            "acknowledged": alert.acknowledged, "created_at": alert.created_at.isoformat(),
            "correct": alert.correct,
        }

    def chat_lines(self, db: Session) -> list[str]:
        positions = db.query(Position).filter(Position.status == "open").all()
        if not positions:
            return []
        lines = ["User's current holdings (monitored):"]
        for p in positions:
            d = self.to_payload(p)
            lines.append(
                f"- {d['name']} ({d['symbol']}): {d['quantity']:g} @ ₹{d['avg_price']:,.2f}, now "
                f"{'₹' + format(d['last_price'], ',.2f') if d['last_price'] else 'n/a'}, P&L "
                f"₹{d['unrealized_pnl']:,.0f} ({d['unrealized_pct'] if d['unrealized_pct'] is not None else 0:+.1f}%), "
                f"stop {d['stop_loss']}, target {d['target']}, loss risk {d['loss_risk'] or 0:.0f}/100"
                + (f" ({'; '.join(d['risk_reasons'][:3])})" if d['risk_reasons'] else ""))
        return lines


position_service = PositionService()
