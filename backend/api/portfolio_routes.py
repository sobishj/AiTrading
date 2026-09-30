"""
Holdings API: record buys/sells, edit stop-loss/target, list holdings with
live risk, and the alerts the monitor raises.
"""
import asyncio
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from database import get_db
from market_service import market_service
from models import Position, PositionAlert, Stock
from position_service import position_service
from ranking_service import ranking_service
from utils.validators import validate_symbol

router = APIRouter()
_learning_tasks: set = set()


def _learn_from_trades() -> None:
    """Re-analyse your trading style after any recorded trade (background; never blocks or fails the trade)."""
    from trader_profile_service import refresh_in_background
    task = asyncio.create_task(refresh_in_background())
    _learning_tasks.add(task)
    task.add_done_callback(_learning_tasks.discard)


class BuyRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    quantity: float = Field(..., gt=0)
    price: Optional[float] = Field(default=None, gt=0, description="Defaults to the current price")
    trade_date: Optional[date] = None
    stop_loss: Optional[float] = Field(default=None, gt=0, description="Defaults to the AI plan's stop-loss")
    target: Optional[float] = Field(default=None, gt=0, description="Defaults to the AI plan's target")
    notes: Optional[str] = Field(default=None, max_length=500)


class SellRequest(BaseModel):
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    trade_date: Optional[date] = None


class UpdateRequest(BaseModel):
    stop_loss: Optional[float] = Field(default=None, ge=0, description="0 clears it")
    target: Optional[float] = Field(default=None, ge=0, description="0 clears it")
    notes: Optional[str] = Field(default=None, max_length=500)


class TransactionUpdate(BaseModel):
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    trade_date: date


class AckRequest(BaseModel):
    ids: Optional[list[int]] = None   # omit to acknowledge all


def _get_position(db: Session, position_id: int) -> Position:
    position = db.get(Position, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Holding not found")
    return position


@router.get("/positions")
async def list_positions(status: Literal["open", "closed", "all"] = "open", db: Session = Depends(get_db)):
    query = db.query(Position)
    if status != "all":
        query = query.filter(Position.status == status)
    rows = query.order_by(desc(Position.created_at)).all()
    return [position_service.to_payload(p) for p in rows]


@router.get("/positions/by-symbol/{symbol}")
async def get_position_for_symbol(symbol: str, db: Session = Depends(get_db)):
    """The open holding for a stock (or null) — used by the Trade Plan panel."""
    symbol = validate_symbol(symbol)
    position = (db.query(Position).join(Stock)
                .filter(Stock.symbol == symbol, Position.status == "open").first())
    return position_service.to_payload(position) if position else None


@router.post("/positions", status_code=201)
async def buy(payload: BuyRequest, db: Session = Depends(get_db)):
    """Record shares you bought. Adds to an existing holding (averaging the cost) if you already hold it."""
    from main import run_position_monitor

    symbol = validate_symbol(payload.symbol)
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if stock is None:
        meta = await market_service.validate_symbol_exists(symbol)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"No NSE market data found for {symbol}")
        from api.routes import _display_name
        name = _display_name(meta, symbol)
        stock = Stock(symbol=symbol, name=name, instrument_type="equity", watchlist_status="inactive",
                      keywords=",".join(sorted({name, symbol})))
        db.add(stock)
        db.commit()
        db.refresh(stock)

    item = ranking_service.get_cached_ranked_stock(symbol) or await ranking_service.rank_stock(stock)
    price = payload.price or (item.technical.close if item and item.technical.close else None)
    if not price:
        raise HTTPException(status_code=422, detail="Enter the price you bought at (no live price available)")
    # The plan's ATR-based stop/target make sense as protective defaults whatever the rating.
    plan = item.plan if item and item.plan else None
    # Your own levels are validated; AI defaults that don't fit your buy price (e.g. bought well
    # above today's price) are simply left empty — the holding window then suggests fitting ones.
    if payload.stop_loss is not None and payload.stop_loss >= price:
        raise HTTPException(status_code=422, detail="Stop-loss must be below the buy price")
    if payload.target is not None and payload.target <= price:
        raise HTTPException(status_code=422, detail="Target must be above the buy price")
    stop = payload.stop_loss if payload.stop_loss is not None else (
        plan.stop_loss if plan and plan.stop_loss < price else None)
    target = payload.target if payload.target is not None else (
        plan.target if plan and plan.target > price else None)

    position = position_service.buy(db, stock, payload.quantity, float(price), payload.trade_date or date.today(),
                                    stop, target, payload.notes)
    await run_position_monitor()   # assess it right away
    _learn_from_trades()
    db.refresh(position)
    return position_service.to_payload(position)


@router.patch("/positions/{position_id}")
async def update_position(position_id: int, payload: UpdateRequest, db: Session = Depends(get_db)):
    """Change your stop-loss / target / notes (0 clears a level)."""
    from main import run_position_monitor
    position = _get_position(db, position_id)
    if payload.stop_loss is not None:
        position.stop_loss = payload.stop_loss or None
    if payload.target is not None:
        position.target = payload.target or None
    if payload.notes is not None:
        position.notes = payload.notes or None
    db.commit()
    await run_position_monitor()    # re-check against the new levels (and refresh the suggestion) right away
    db.refresh(position)
    return position_service.to_payload(position)


@router.post("/positions/{position_id}/sell")
async def sell(position_id: int, payload: SellRequest, db: Session = Depends(get_db)):
    """Record a partial or full sale. The realised trade feeds the learning engine."""
    position = _get_position(db, position_id)
    if position.status != "open":
        raise HTTPException(status_code=409, detail="This holding is already closed")
    try:
        position_service.sell(db, position, payload.quantity, payload.price, payload.trade_date or date.today())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _learn_from_trades()
    db.refresh(position)
    return position_service.to_payload(position)


@router.patch("/positions/{position_id}/transactions/{tx_id}")
async def edit_transaction(position_id: int, tx_id: int, payload: TransactionUpdate, db: Session = Depends(get_db)):
    """Correct a recorded buy or sale; quantity, cost and P&L are recomputed from all transactions."""
    from main import run_position_monitor
    position = _get_position(db, position_id)
    try:
        position_service.edit_transaction(db, position, tx_id, payload.quantity, payload.price, payload.trade_date)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _learn_from_trades()
    await run_position_monitor()
    db.refresh(position)
    return position_service.to_payload(position)


@router.delete("/positions/{position_id}/transactions/{tx_id}")
async def delete_transaction(position_id: int, tx_id: int, db: Session = Depends(get_db)):
    """Delete one buy or sale (e.g. undo a sale). Deleting the last buy deletes the holding."""
    from main import run_position_monitor
    position = _get_position(db, position_id)
    try:
        still_exists = position_service.delete_transaction(db, position, tx_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _learn_from_trades()
    if not still_exists:
        return None
    await run_position_monitor()
    db.refresh(position)
    return position_service.to_payload(position)


@router.get("/positions/learning")
async def holdings_learning(db: Session = Depends(get_db)):
    """How reliable the risk warnings have been, the adaptive threshold, and realised results."""
    return position_service.alert_stats(db)


@router.get("/positions/{position_id}")
async def get_position(position_id: int, db: Session = Depends(get_db)):
    return position_service.to_payload(_get_position(db, position_id))


@router.delete("/positions/{position_id}", status_code=204)
async def delete_position(position_id: int, db: Session = Depends(get_db)):
    """Delete a holding entered by mistake (also removes its learning rows)."""
    position_service.delete(db, _get_position(db, position_id))
    _learn_from_trades()


@router.post("/positions/check-now")
async def check_now(db: Session = Depends(get_db)):
    from main import run_position_monitor
    await run_position_monitor()
    rows = db.query(Position).filter(Position.status == "open").all()
    return [position_service.to_payload(p) for p in rows]


@router.get("/alerts")
async def list_alerts(limit: int = 50, unread_only: bool = False, db: Session = Depends(get_db)):
    query = db.query(PositionAlert)
    if unread_only:
        query = query.filter(PositionAlert.acknowledged.is_(False))
    rows = query.order_by(desc(PositionAlert.created_at)).limit(min(limit, 200)).all()
    return {"unread": db.query(PositionAlert).filter(PositionAlert.acknowledged.is_(False)).count(),
            "alerts": [position_service.alert_payload(a) for a in rows]}


@router.post("/alerts/ack")
async def acknowledge_alerts(payload: AckRequest, db: Session = Depends(get_db)):
    query = db.query(PositionAlert).filter(PositionAlert.acknowledged.is_(False))
    if payload.ids:
        query = query.filter(PositionAlert.id.in_(payload.ids))
    count = query.update({PositionAlert.acknowledged: True}, synchronize_session=False)
    db.commit()
    return {"acknowledged": count}


# ----------------------------------------------------------------------
# Trades reported in chat ("I bought 10 at 265 and sold at 280") and your trading style
# ----------------------------------------------------------------------
class TradeActionIn(BaseModel):
    side: Literal["BUY", "SELL"]
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    trade_date: Optional[date] = None


class RecordTradesRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    actions: list[TradeActionIn] = Field(..., min_length=1, max_length=10)


async def build_trade_proposal(db: Session, intent, symbol: Optional[str]) -> dict:
    """
    What a chat message reported, checked against real data, for you to confirm. Nothing is saved here.
    Prices are compared with that day's actual trading range; missing quantities stay empty.
    """
    import pandas as pd

    warnings, needs = [], []
    stock = db.query(Stock).filter(Stock.symbol == symbol).first() if symbol else None
    if stock is None:
        needs.append("symbol")
    held = None
    if stock is not None:
        pos = db.query(Position).filter(Position.stock_id == stock.id, Position.status == "open").first()
        held = float(pos.quantity) if pos else 0.0
    candles = None
    if stock is not None:
        oldest = min((a.trade_date for a in intent.actions if a.trade_date), default=date.today())
        try:
            candles = await market_service.get_candles_for_symbol(stock.symbol, days=(date.today() - oldest).days + 10)
        except Exception:  # noqa: BLE001
            candles = None
    actions = []
    running = held or 0.0
    for a in intent.actions:
        day = a.trade_date or date.today()
        check = None
        if candles is not None and not candles.empty and "date" in candles.columns:
            dates = pd.to_datetime(candles["date"]).dt.date
            rows = candles[dates == day]
            if not rows.empty:
                low, high = float(rows.iloc[-1]["low"]), float(rows.iloc[-1]["high"])
                check = {"day_low": round(low, 2), "day_high": round(high, 2)}
                if not (low * 0.97 <= a.price <= high * 1.03):
                    warnings.append(f"Rs {a.price:g} is outside {stock.symbol}'s actual range on {day:%d %b} "
                                    f"(Rs {low:.2f} to {high:.2f}) - check the price or the stock.")
            else:
                warnings.append(f"No trading data for {stock.symbol} on {day:%d %b} (holiday or weekend?) - check the date.")
        if a.quantity is None:
            needs.append(f"quantity for the {a.side.lower()}")
        if a.side == "BUY":
            running += a.quantity or 0
        elif stock is not None and running == 0:
            warnings.append(f"No recorded holding of {stock.symbol} to sell - add the buy too.")
        elif stock is not None and a.quantity is not None and a.quantity > running:
            warnings.append(f"Selling {a.quantity:g} but only {running:g} {stock.symbol} recorded as held.")
        actions.append({"side": a.side, "quantity": a.quantity, "price": a.price, "trade_date": day.isoformat(),
                        "price_check": check})
    return {"symbol": stock.symbol if stock else None, "name": stock.name if stock else None,
            "held_quantity": held, "actions": actions, "warnings": warnings, "needs": needs}


@router.post("/trades/record")
async def record_trades(payload: RecordTradesRequest, db: Session = Depends(get_db)):
    """Record trades you confirmed from chat, in order (e.g. a buy then a sale), exactly like the Holdings buttons."""
    symbol = validate_symbol(payload.symbol)
    recorded, result = [], None
    for a in payload.actions:
        if a.side == "BUY":
            result = await buy(BuyRequest(symbol=symbol, quantity=a.quantity, price=a.price, trade_date=a.trade_date), db)
        else:
            pos = (db.query(Position).join(Stock)
                   .filter(Stock.symbol == symbol, Position.status == "open").first())
            if pos is None:
                raise HTTPException(status_code=409, detail=f"No open holding of {symbol} to sell - record the buy first")
            result = await sell(pos.id, SellRequest(quantity=a.quantity, price=a.price, trade_date=a.trade_date), db)
        recorded.append({"side": a.side, "quantity": a.quantity, "price": a.price,
                         "trade_date": (a.trade_date or date.today()).isoformat()})
    return {"symbol": symbol, "recorded": recorded, "position": result}


@router.get("/profile/trading-style")
async def trading_style(db: Session = Depends(get_db)):
    """Your trading style measured from your recorded trades (buys, sells, and Zerodha uploads)."""
    from trader_profile_service import trader_profile_service
    return trader_profile_service.get(db) or {"stats": {"entries": 0}, "style_notes": [], "entries": [], "exits": []}


@router.post("/profile/trading-style/refresh")
async def refresh_trading_style(db: Session = Depends(get_db)):
    from trader_profile_service import trader_profile_service
    return await trader_profile_service.refresh(db)
