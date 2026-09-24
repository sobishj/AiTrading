"""
API routes for TradeAI: live ranking, trade plans, analysis, chat, learning
(trade uploads, strategy performance), market context, morning brief,
settings, and the real-time WebSocket updates feed.
"""
import json
from datetime import date, datetime, timedelta
from typing import Optional

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ai_analyst_service import ai_analyst_service
from analysis_service import analysis_service
from brief_service import brief_service
from config import settings
from database import get_db
from insight_service import insight_service
from kite_service import kite_service
from learning_service import learning_service
from llm_service import llm_service
from market_service import market_service
from models import MarketSnapshot, Recommendation, Stock, TradeHistory, UserChat, tracked_stock_filter
from ranking_service import RANKING_MAX_AGE_FOR_READS, RankedStock, ranking_service
from schemas import (
    AddStockRequest, AppSettingsResponse, ChatMessageRequest, ChatMessageResponse, MarketContextResponse,
    MorningBriefResponse, OrderBasketResponse, RecommendationResponse, RefreshIntervalUpdateRequest,
    RiskSettingsUpdateRequest, StockAnalysisResponse, StockDetailResponse, StockNarrativeResponse,
    StockResponse, StrategyPerformanceResponse, TechnicalIndicatorsResponse, TradeHistoryResponse,
    TradePlanResponse, TradeUploadResponse,
)
from utils.logger import get_logger
from utils.validators import validate_chat_message, validate_symbol

logger = get_logger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_SNAPSHOT_INTERVAL = timedelta(hours=1)


# ---------------------------------------------------------------------------
# WebSocket connection manager (shared with main.py's background loops)
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WebSocket connected (%d active)", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info("WebSocket disconnected (%d active)", len(self.active_connections))

    async def broadcast(self, message: dict) -> None:
        stale = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:  # noqa: BLE001
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


connection_manager = ConnectionManager()


async def refresh_and_broadcast(db: Session, trigger: Optional[str] = None) -> list[RankedStock]:
    """Force a full re-rank, record top BUY ideas, and push the new order to every client."""
    ranked = await ranking_service.run_full_ranking(db, force=True)
    await ranking_service.persist_top_recommendations(db, ranked)
    await connection_manager.broadcast(ranking_service.build_ranking_broadcast_payload(ranked, trigger))
    return ranked


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_stock_or_404(db: Session, symbol: str) -> Stock:
    symbol = validate_symbol(symbol)
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {symbol} not found — add it to the watchlist first")
    return stock


async def _get_ranked_stock(db: Session, stock: Stock) -> RankedStock:
    """
    The stock's entry from the latest ranking run when it's recent (keeps the
    detail views consistent with the list); otherwise score it on its own
    using the last run's context.
    """
    cached = ranking_service.get_cached_ranked_stock(stock.symbol)
    if cached is not None:
        return cached
    if ranking_service.last_ranked_at is None:
        ranked = await ranking_service.run_full_ranking(db)
        found = next((r for r in ranked if r.symbol == stock.symbol), None)
        if found is not None:
            return found
    return await ranking_service.rank_stock(stock)


def _display_name(meta: dict, symbol: str) -> str:
    """Readable company name from Yahoo meta: long name minus "Limited", else a title-cased short name."""
    for key in ("longName", "shortName"):
        raw = (meta.get(key) or "").strip()
        for suffix in (" Limited", " Ltd.", " Ltd", " LTD", " LIMITED"):
            raw = raw.removesuffix(suffix)
        if raw and (key == "shortName" or len(raw) <= 28):
            return (raw.title() if raw.isupper() else raw)[:100]
    return symbol


def _rec_response(rec: Recommendation, symbol: Optional[str]) -> RecommendationResponse:
    return RecommendationResponse(
        id=rec.id, stock_id=rec.stock_id, symbol=symbol, entry_price=float(rec.entry_price),
        target_price=float(rec.target_price), stop_loss=float(rec.stop_loss),
        holding_period=rec.holding_period, risk_level=rec.risk_level, reasoning=rec.reasoning,
        timestamp=rec.timestamp, confidence_score=float(rec.confidence_score),
        action=rec.action, instrument=rec.instrument, strategy=rec.strategy,
        entry_low=float(rec.entry_low) if rec.entry_low is not None else None,
        entry_high=float(rec.entry_high) if rec.entry_high is not None else None,
        risk_reward=float(rec.risk_reward) if rec.risk_reward is not None else None,
        ai_commentary=rec.ai_commentary,
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
@router.get("/status")
async def get_status():
    """Data source, LLM availability and ranking freshness, for the top bar."""
    return {
        "data_source": market_service.data_source,
        "kite_connected": kite_service.is_configured,
        "llm_available": await llm_service.is_available(),
        "llm_model": settings.LLM_MODEL,
        "last_ranked_at": ranking_service.last_ranked_at.isoformat() if ranking_service.last_ranked_at else None,
        "market_regime": ranking_service.context.market_regime,
    }


# ---------------------------------------------------------------------------
# Stocks / watchlist
# ---------------------------------------------------------------------------
def _stock_response(stock: Stock, r: Optional[RankedStock], list_rank: Optional[int] = None) -> StockResponse:
    return StockResponse(
        id=stock.id, symbol=stock.symbol, name=stock.name, sector=stock.sector,
        instrument_type=stock.instrument_type,
        current_price=float(stock.current_price) if stock.current_price is not None else None,
        watchlist_status=stock.watchlist_status, last_updated=stock.last_updated,
        rank=r.rank if r else None,
        conviction_score=r.conviction_score if r else None,
        previous_rank=r.previous_rank if r else None,
        action=r.action if r else None,
        strategy=r.strategy if r else None,
        change_pct=r.technical.change_pct if r else None,
        change_reason=r.change_reason if r else None,
        in_auto_list=stock.watchlist_status == "active",
        in_manual_list=bool(stock.in_manual_list),
        list_rank=list_rank,
    )


@router.get("/stocks", response_model=list[StockResponse])
async def list_stocks(list_name: Literal["auto", "manual", "all"] = Query("auto", alias="list"),
                      db: Session = Depends(get_db)):
    """
    A list in live-ranking order (strongest opportunity first):
    - auto: the AI-managed universe (default)
    - manual: the user's own list (add/remove via /watchlist/manual)
    - all: everything the engine tracks
    """
    ranked = await ranking_service.run_full_ranking(db, max_age=RANKING_MAX_AGE_FOR_READS)
    rank_lookup = {item.symbol: item for item in ranked}

    query = db.query(Stock)
    if list_name == "auto":
        query = query.filter(Stock.watchlist_status == "active")
    elif list_name == "manual":
        query = query.filter(Stock.in_manual_list.is_(True))
    else:
        query = query.filter(tracked_stock_filter())
    stocks = query.all()
    stocks.sort(key=lambda st: (rank_lookup.get(st.symbol) is None,
                                rank_lookup[st.symbol].rank if st.symbol in rank_lookup else 0, st.name))
    return [_stock_response(stock, rank_lookup.get(stock.symbol), idx) for idx, stock in enumerate(stocks, start=1)]


@router.post("/watchlist/manual", response_model=StockResponse, status_code=201)
async def add_to_manual_list(payload: AddStockRequest, db: Session = Depends(get_db)):
    """
    Add any NSE share to the user's Manual list (it's validated against market
    data first). It is ranked immediately, so its chart, trade plan and
    analysis are available as soon as this returns.
    """
    symbol = validate_symbol(payload.symbol)
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if stock is None:
        meta = await market_service.validate_symbol_exists(symbol)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"No NSE market data found for {symbol}")
        name = payload.name or _display_name(meta, symbol)
        stock = Stock(symbol=symbol, name=name, sector=payload.sector, instrument_type="equity",
                      watchlist_status="inactive", keywords=",".join(sorted({name, symbol})))
        db.add(stock)
    elif stock.name == stock.symbol:
        # Placeholder rows (e.g. created by a tradebook import) get a real name.
        meta = await market_service.validate_symbol_exists(symbol)
        if meta:
            stock.name = _display_name(meta, symbol)
    stock.in_manual_list = True
    db.commit()
    db.refresh(stock)

    ranked = await refresh_and_broadcast(db, trigger=f"{stock.name} added to your list")
    item = next((r for r in ranked if r.symbol == symbol), None)
    return _stock_response(stock, item)


@router.delete("/watchlist/manual/{symbol}", status_code=204)
async def remove_from_manual_list(symbol: str, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Remove a share from the Manual list (the Auto universe and all history are unaffected)."""
    stock = _get_stock_or_404(db, symbol)
    if not stock.in_manual_list:
        return
    stock.in_manual_list = False
    db.commit()

    async def rerank() -> None:
        from database import db_session
        with db_session() as bg_db:
            await refresh_and_broadcast(bg_db, trigger=f"{stock.name} removed from your list")

    background.add_task(rerank)


@router.post("/stocks", response_model=StockResponse, status_code=201)
async def add_stock(payload: AddStockRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Add (or re-activate) an NSE stock in the Auto universe after checking it has market data."""
    symbol = validate_symbol(payload.symbol)
    meta = await market_service.validate_symbol_exists(symbol)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"No NSE market data found for {symbol}")

    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    name = payload.name or _display_name(meta, symbol)
    if stock is None:
        stock = Stock(symbol=symbol, name=name, sector=payload.sector, instrument_type="equity",
                      watchlist_status="active", keywords=",".join({name, symbol}))
        db.add(stock)
    else:
        stock.watchlist_status = "active"
        if stock.name == stock.symbol:
            stock.name = name
        if payload.sector:
            stock.sector = payload.sector
    db.commit()
    db.refresh(stock)

    async def rerank() -> None:
        from database import db_session
        with db_session() as bg_db:
            await refresh_and_broadcast(bg_db, trigger=f"{symbol} added to watchlist")

    background.add_task(rerank)
    return _stock_response(stock, None)


@router.delete("/stocks/{symbol}", status_code=204)
async def remove_stock(symbol: str, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Remove a stock from the Auto universe (history and recommendations are kept)."""
    stock = _get_stock_or_404(db, symbol)
    stock.watchlist_status = "inactive"
    db.commit()

    async def rerank() -> None:
        from database import db_session
        with db_session() as bg_db:
            await refresh_and_broadcast(bg_db, trigger=f"{stock.symbol} removed from watchlist")

    background.add_task(rerank)


@router.get("/stocks/{symbol}/candles")
async def get_stock_candles(symbol: str, interval: str = "day", days: int = 180):
    """OHLCV candles for chart rendering (Kite when connected, otherwise Yahoo Finance)."""
    symbol = validate_symbol(symbol)
    candles = await market_service.get_candles_for_symbol(symbol, days=days, interval=interval)
    return _candles_payload(candles)


def _candles_payload(candles) -> list[dict]:
    if candles.empty:
        return []
    candles = candles.rename(columns={c: c.lower() for c in candles.columns})
    return [
        {
            "time": row["date"].isoformat() if hasattr(row.get("date"), "isoformat") else row.get("date"),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": int(row["volume"]) if row.get("volume") is not None else 0,
        }
        for _, row in candles.iterrows()
    ]


@router.get("/stocks/{symbol}/chart")
async def get_stock_chart(symbol: str, interval: str = "day", days: int = 365, db: Session = Depends(get_db)):
    """Candles plus EMA/RSI/MACD overlays and the key price levels (S/R and trade-plan lines)."""
    symbol = validate_symbol(symbol)
    candles = await market_service.get_candles_for_symbol(symbol, days=days, interval=interval)
    levels: dict[str, Optional[float]] = {}
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if stock is not None:
        item = await _get_ranked_stock(db, stock)
        t, plan = item.technical, item.plan
        levels = {
            "support": t.support, "resistance": t.resistance, "high_52w": t.high_52w,
            "entry_low": plan.entry_low if plan and plan.action != "AVOID" else None,
            "entry_high": plan.entry_high if plan and plan.action != "AVOID" else None,
            "stop_loss": plan.stop_loss if plan and plan.action != "AVOID" else None,
            "target": plan.target if plan and plan.action != "AVOID" else None,
        }
    return {
        "symbol": symbol,
        "interval": interval,
        "source": market_service.data_source,
        "candles": _candles_payload(candles),
        "overlays": analysis_service.chart_overlays(candles),
        "levels": levels,
    }


@router.get("/stocks/{symbol}", response_model=StockDetailResponse)
async def get_stock_detail(symbol: str, db: Session = Depends(get_db)):
    """Full detail + latest technical snapshot for a single stock."""
    stock = _get_stock_or_404(db, symbol)
    ranked = await _get_ranked_stock(db, stock)
    latest_rec = (
        db.query(Recommendation)
        .filter(Recommendation.stock_id == stock.id)
        .order_by(desc(Recommendation.timestamp))
        .first()
    )
    t = ranked.technical
    technical_resp = TechnicalIndicatorsResponse(
        stock_id=stock.id, rsi=t.rsi, macd=t.macd, volume=t.volume, ema_20=t.ema_20, ema_50=t.ema_50,
        timestamp=datetime.utcnow(), macd_signal=t.macd_signal, ema_200=t.ema_200, atr=t.atr,
        volume_ratio=t.volume_ratio, support=t.support, resistance=t.resistance, high_52w=t.high_52w,
        low_52w=t.low_52w, return_20d=t.return_20d, relative_strength_20d=t.relative_strength_20d,
        technical_score=t.technical_score, trend=t.trend, setups=t.setups,
    )
    return StockDetailResponse(
        id=stock.id, symbol=stock.symbol, name=stock.name, sector=stock.sector,
        instrument_type=stock.instrument_type,
        current_price=t.close if t.close is not None else (
            float(stock.current_price) if stock.current_price is not None else None),
        watchlist_status=stock.watchlist_status, last_updated=stock.last_updated,
        rank=ranked.rank or None, conviction_score=ranked.conviction_score,
        previous_rank=ranked.previous_rank, action=ranked.action, strategy=ranked.strategy,
        change_pct=t.change_pct, change_reason=ranked.change_reason,
        technical=technical_resp,
        latest_recommendation=_rec_response(latest_rec, stock.symbol) if latest_rec else None,
    )


@router.get("/stocks/{symbol}/trade-plan", response_model=TradePlanResponse)
async def get_trade_plan(symbol: str, db: Session = Depends(get_db)):
    """
    The Trade Plan panel for any stock. BUY plans are backed by a persisted
    recommendation (reused while the setup is unchanged, never duplicated).
    """
    stock = _get_stock_or_404(db, symbol)
    item = await _get_ranked_stock(db, stock)
    if item.plan is None:
        raise HTTPException(status_code=404, detail=f"No price data available for {stock.symbol}")
    rec = await ranking_service.generate_recommendation(db, item)
    cfg = ranking_service.get_or_create_settings(db)
    return ranking_service.plan_payload(item, cfg, rec)


@router.get("/stocks/{symbol}/analysis", response_model=StockAnalysisResponse)
async def get_stock_analysis(symbol: str, db: Session = Depends(get_db)):
    """Why the AI ranked this stock: market context, confirmation, news, risks, history, exit logic."""
    stock = _get_stock_or_404(db, symbol)
    item = await _get_ranked_stock(db, stock)
    return insight_service.build_analysis(db, item)


@router.get("/stocks/{symbol}/analysis/narrative", response_model=StockNarrativeResponse)
async def get_stock_narrative(symbol: str, db: Session = Depends(get_db)):
    """Optional analyst note from the local LLM (slow; null when the model is offline)."""
    stock = _get_stock_or_404(db, symbol)
    item = await _get_ranked_stock(db, stock)
    text = await insight_service.narrative(db, item)
    return StockNarrativeResponse(symbol=stock.symbol, narrative=text, available=text is not None,
                                  generated_at=datetime.utcnow())


@router.get("/stocks/{symbol}/order-basket", response_model=OrderBasketResponse)
async def get_order_basket(symbol: str, db: Session = Depends(get_db)):
    """
    A prepared LIMIT order for Kite Publisher. TradeAI never places orders:
    the browser posts this basket to Zerodha, where the user reviews and
    confirms it themselves.
    """
    stock = _get_stock_or_404(db, symbol)
    item = await _get_ranked_stock(db, stock)
    plan = item.plan
    if plan is None or plan.action == "AVOID":
        raise HTTPException(status_code=409, detail=f"No actionable plan for {stock.symbol} right now")
    cfg = ranking_service.get_or_create_settings(db)
    sizing = ranking_service.position_size(plan, cfg)
    order = kite_service.prepare_order_data(
        stock.symbol, "BUY", max(sizing["quantity"], 1), order_type="LIMIT", price=plan.entry_high, product="CNC",
    )
    order.pop("note", None)
    order["variety"] = "regular"
    return OrderBasketResponse(
        api_key=kite_service.api_key if kite_service.has_api_key else None,
        basket=[order],
        kite_url=kite_service.generate_trade_url(stock.symbol),
        publisher_available=kite_service.has_api_key,
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
@router.post("/chat", response_model=ChatMessageResponse)
async def chat(request: ChatMessageRequest, db: Session = Depends(get_db)):
    """Trading Coach chat, grounded in the live ranking, the selected stock and past conversation."""
    message = validate_chat_message(request.message)
    ranked = await ranking_service.run_full_ranking(db, max_age=RANKING_MAX_AGE_FOR_READS)
    by_symbol = {r.symbol: r for r in ranked}

    selected = None
    if request.stock_context:
        symbol = validate_symbol(request.stock_context)
        selected = by_symbol.get(symbol)
        if selected is None:
            stock = db.query(Stock).filter(Stock.symbol == symbol).first()
            if stock is not None:
                selected = await ranking_service.rank_stock(stock)

    context_lines = insight_service.chat_context(db, ranked, selected)
    # "Compare Tata Motors with Reliance": add detail for every stock the question names.
    for symbol in insight_service.find_symbol_mentions(db, message):
        item = by_symbol.get(symbol)
        if item is not None and item is not selected:
            context_lines.append(f"{item.name} ({symbol}) detail: {ranking_service.build_reasoning(item)}")

    ai_response = await llm_service.chat(
        message, stock_context=request.stock_context, context_snippets=context_lines,
        history=insight_service.chat_history(db),
        focus=(selected.name, selected.symbol) if selected is not None else None,
    )

    chat_row = UserChat(user_message=message, ai_response=ai_response,
                        stock_context=request.stock_context, timestamp=datetime.utcnow())
    db.add(chat_row)
    db.commit()
    return ChatMessageResponse(id=chat_row.id, user_message=message, ai_response=ai_response,
                               stock_context=request.stock_context, timestamp=chat_row.timestamp)


@router.get("/chat/history", response_model=list[ChatMessageResponse])
async def get_chat_history(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.query(UserChat).order_by(UserChat.timestamp.desc()).limit(min(limit, 200)).all()
    return [ChatMessageResponse(id=r.id, user_message=r.user_message, ai_response=r.ai_response,
                                stock_context=r.stock_context, timestamp=r.timestamp) for r in reversed(rows)]


@router.delete("/chat/history", status_code=204)
async def clear_chat_history(db: Session = Depends(get_db)):
    db.query(UserChat).delete()
    db.commit()


# ---------------------------------------------------------------------------
# Recommendations / morning brief
# ---------------------------------------------------------------------------
@router.get("/recommendations", response_model=Optional[RecommendationResponse])
async def get_recommendations(db: Session = Depends(get_db)):
    """Today's single best actionable pick (reuses the existing recommendation while it's valid)."""
    ranked = await ranking_service.run_full_ranking(db, max_age=RANKING_MAX_AGE_FOR_READS)
    best = ranking_service.get_best_pick(ranked)
    if not best:
        return None
    rec = await ranking_service.generate_recommendation(db, best)
    return _rec_response(rec, best.symbol) if rec else None


@router.get("/recommendations/history", response_model=list[dict])
async def get_recommendation_history(limit: int = 50, db: Session = Depends(get_db)):
    """Market memory: past recommendations with their graded outcome."""
    recs = db.query(Recommendation).order_by(desc(Recommendation.timestamp)).limit(min(limit, 200)).all()
    outcomes: dict[int, TradeHistory] = {}
    for th in db.query(TradeHistory).filter(TradeHistory.recommendation_id.in_([r.id for r in recs] or [-1])).all():
        if th.recommendation_id not in outcomes or th.source == "zerodha":
            outcomes[th.recommendation_id] = th
    result = []
    for rec in recs:
        outcome = outcomes.get(rec.id)
        result.append({
            **_rec_response(rec, rec.stock.symbol if rec.stock else None).model_dump(mode="json"),
            "outcome": outcome.actual_outcome if outcome else "open",
            "exit_price": float(outcome.exit_price) if outcome and outcome.exit_price is not None else None,
            "outcome_source": outcome.source if outcome else None,
        })
    return result


@router.get("/morning-brief", response_model=MorningBriefResponse)
async def get_morning_brief(db: Session = Depends(get_db)):
    """Today's pre-market brief; generated on first request if the scheduler hasn't run yet."""
    brief = brief_service.get_today(db) or await brief_service.generate(db)
    return brief_service.to_payload(brief)


@router.post("/morning-brief/regenerate", response_model=MorningBriefResponse)
async def regenerate_morning_brief(db: Session = Depends(get_db)):
    brief = await brief_service.generate(db, force=True)
    return brief_service.to_payload(brief)


# ---------------------------------------------------------------------------
# Trade history / learning
# ---------------------------------------------------------------------------
@router.post("/upload-trades", response_model=TradeUploadResponse)
async def upload_trades(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Upload a Zerodha tradebook (CSV/XLSX from Console → Reports → Tradebook).
    Trades are FIFO-matched into round trips, de-duplicated, linked to the
    recommendations that preceded them, and then a learning cycle runs.
    """
    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Trade report is larger than 10 MB")
    try:
        df = kite_service.parse_trade_report(file_bytes, file.filename or "trades.csv")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Failed to parse trade report: {exc}")

    result = learning_service.import_tradebook(db, df)
    cycle = await learning_service.run_learning_cycle(db)
    return TradeUploadResponse(**result, graded=cycle["graded"], recalibration=cycle["recalibration"])


@router.get("/trade-history", response_model=list[TradeHistoryResponse])
async def get_trade_history(limit: int = 50, source: Optional[str] = None, db: Session = Depends(get_db)):
    """Past trades: graded recommendations ("auto") and imported Zerodha trades ("zerodha")."""
    query = db.query(TradeHistory)
    if source:
        query = query.filter(TradeHistory.source == source)
    rows = query.order_by(desc(TradeHistory.execution_date), desc(TradeHistory.id)).limit(min(limit, 500)).all()
    return [
        TradeHistoryResponse(
            id=r.id, stock_id=r.stock_id, symbol=r.stock.symbol if r.stock else None,
            execution_date=r.execution_date, entry_price=float(r.entry_price),
            exit_price=float(r.exit_price) if r.exit_price is not None else None,
            profit_loss=float(r.profit_loss) if r.profit_loss is not None else None,
            predicted_target=float(r.predicted_target) if r.predicted_target is not None else None,
            actual_outcome=r.actual_outcome, timestamp=r.timestamp,
            quantity=float(r.quantity) if r.quantity is not None else None,
            exit_date=r.exit_date, source=r.source,
            strategy=r.recommendation.strategy if r.recommendation else None,
            recommendation_id=r.recommendation_id,
        )
        for r in rows
    ]


@router.get("/strategies", response_model=list[StrategyPerformanceResponse])
async def get_strategy_performance(db: Session = Depends(get_db)):
    """Strategy Library (PRD §15): performance learned from graded calls and real trades."""
    return learning_service.strategy_performance(db)


@router.post("/learning/run")
async def run_learning(db: Session = Depends(get_db)):
    """Grade matured recommendations and recalibrate weights now (also runs daily after close)."""
    return await learning_service.run_learning_cycle(db)


# ---------------------------------------------------------------------------
# AI analyst (Qwen) learning loop
# ---------------------------------------------------------------------------
@router.get("/ai/performance")
async def get_ai_performance(db: Session = Depends(get_db)):
    """Qwen's forecasting track record: hit rate, Brier score, baseline, earned trust, weekly trend."""
    from main import market_is_open

    version, lessons = ai_analyst_service.current_lessons(db)
    return {**ai_analyst_service.performance(db), "lessons_version": version, "lessons": lessons,
            "enabled": settings.AI_ANALYST_ENABLED, "llm_available": await llm_service.is_available(),
            "practice": ai_analyst_service.performance(db, kind="practice"),
            "practice_enabled": ai_analyst_service.practice_enabled(db),
            "practice_today": ai_analyst_service.practice_done_today(db),
            "practicing_now": (not market_is_open()) and ai_analyst_service.practice_enabled(db),
            "outlook": ai_analyst_service.outlook_performance(db)}


@router.put("/ai/practice")
async def set_ai_practice(payload: dict, db: Session = Depends(get_db)):
    """Turn historical-chart practice (market closed) on or off: {"enabled": true|false}."""
    cfg = ranking_service.get_or_create_settings(db)
    cfg.ai_practice_enabled = bool(payload.get("enabled", True))
    db.commit()
    return {"practice_enabled": cfg.ai_practice_enabled}


@router.get("/ai/outlook")
async def get_ai_outlook(db: Session = Depends(get_db)):
    """Qwen's latest next-session outlook (NIFTY bias + sector impacts), or null."""
    return ai_analyst_service.outlook_payload(ai_analyst_service.latest_outlook(db))


@router.get("/ai/outlooks")
async def get_ai_outlooks(limit: int = 20, db: Session = Depends(get_db)):
    from models import AIMarketOutlook
    rows = db.query(AIMarketOutlook).order_by(AIMarketOutlook.created_at.desc()).limit(min(limit, 200)).all()
    return [ai_analyst_service.outlook_payload(r) for r in rows]


@router.post("/ai/outlook-now", status_code=202)
async def ai_outlook_now():
    from main import enqueue_ai_outlook
    enqueue_ai_outlook()
    return {"status": "queued"}


@router.get("/ai/lessons")
async def get_ai_lessons(db: Session = Depends(get_db)):
    """Every version of the lessons Qwen has written, newest first."""
    from models import AILesson
    rows = db.query(AILesson).order_by(AILesson.version.desc()).limit(50).all()
    return [{"version": r.version, "lessons": r.lessons_text.split("\n"), "created_at": r.created_at.isoformat(),
             "based_on_predictions": r.based_on_predictions,
             "hit_rate_at_creation": float(r.hit_rate_at_creation) if r.hit_rate_at_creation is not None else None}
            for r in rows]


@router.get("/ai/predictions")
async def get_ai_predictions(symbol: Optional[str] = None, limit: int = 50,
                             kind: Literal["live", "practice"] = "live", db: Session = Depends(get_db)):
    """Qwen's forecasts (newest first), graded or pending."""
    from models import AIPrediction
    query = db.query(AIPrediction, Stock).join(Stock, AIPrediction.stock_id == Stock.id)
    if symbol:
        query = query.filter(Stock.symbol == validate_symbol(symbol))
    query = query.filter(AIPrediction.kind == kind)
    order = AIPrediction.id.desc() if kind == "practice" else AIPrediction.prediction_date.desc()
    rows = query.order_by(order, AIPrediction.id.desc()).limit(min(limit, 500)).all()
    return [{
        "id": p.id, "symbol": s.symbol, "name": s.name, "date": p.prediction_date.isoformat(),
        "direction": p.direction, "probability_up": float(p.probability_up),
        "expected_move_pct": float(p.expected_move_pct) if p.expected_move_pct is not None else None,
        "reason": p.reason, "horizon_days": p.horizon_days, "price_at_prediction": float(p.price_at_prediction),
        "lessons_version": p.lessons_version,
        "actual_return_pct": float(p.actual_return_pct) if p.actual_return_pct is not None else None,
        "correct": p.correct, "graded": p.graded_at is not None,
    } for p, s in rows]


@router.post("/ai/forecast-now", status_code=202)
async def ai_forecast_now(force: bool = False):
    """Queue today's forecasts now (stocks already forecast today are skipped unless force=true)."""
    from main import enqueue_ai_forecasts
    enqueue_ai_forecasts(force=force)
    return {"status": "queued"}


@router.post("/ai/reflect", status_code=202)
async def ai_reflect_now(db: Session = Depends(get_db)):
    """Grade due forecasts, then queue a reflection so Qwen rewrites its lessons."""
    graded = await ai_analyst_service.grade_predictions(db)
    from main import enqueue_ai_reflection
    enqueue_ai_reflection(force=True)
    return {"status": "queued", "graded": graded}


@router.get("/ai/training-data")
async def get_ai_training_data(db: Session = Depends(get_db)):
    """Graded forecasts as JSONL (prompt, model answer, outcome) — a dataset for fine-tuning later."""
    rows = [json.dumps(example, ensure_ascii=False) for example in ai_analyst_service.training_examples(db)]
    return StreamingResponse(iter([line + "\n" for line in rows]), media_type="application/x-ndjson",
                             headers={"Content-Disposition": "attachment; filename=tradeai-ai-forecasts.jsonl"})


# ---------------------------------------------------------------------------
# Market context
# ---------------------------------------------------------------------------
@router.get("/market-context", response_model=MarketContextResponse)
async def get_market_context(db: Session = Depends(get_db)):
    """NIFTY trend and regime, India VIX, global cues, FII/DII and top headlines."""
    context = await market_service.summarize_market_context()
    overview = context["overview"]

    latest = db.query(MarketSnapshot).order_by(desc(MarketSnapshot.timestamp)).first()
    if latest is None or datetime.utcnow() - latest.timestamp > _SNAPSHOT_INTERVAL:
        db.add(MarketSnapshot(
            market_date=date.today(), fii_activity=context["fii_activity"], dii_activity=context["dii_activity"],
            news_sentiment=context["news_sentiment"], global_market_summary=context["global_market_summary"],
            context_json=json.dumps({"headlines": context["headlines"], "indices": overview["indices"],
                                     "regime": overview["regime"]}, default=str),
            timestamp=datetime.utcnow(),
        ))
        db.commit()

    return MarketContextResponse(
        market_date=date.today(), fii_activity=context["fii_activity"], dii_activity=context["dii_activity"],
        news_sentiment=context["news_sentiment"], global_market_summary=context["global_market_summary"],
        timestamp=datetime.utcnow(), regime=overview["regime"], indices=overview["indices"],
        nifty=overview["nifty"], headlines=context["headlines"],
    )


# ---------------------------------------------------------------------------
# Manual refresh trigger
# ---------------------------------------------------------------------------
@router.post("/refresh")
async def manual_refresh(db: Session = Depends(get_db)):
    """Manually trigger a full re-evaluation and broadcast the result over WebSocket."""
    ranked = await refresh_and_broadcast(db, trigger="manual refresh")
    best = ranking_service.get_best_pick(ranked)
    return {"status": "refreshed", "stocks_evaluated": len(ranked), "best_pick": best.symbol if best else None}


# ---------------------------------------------------------------------------
# Zerodha Kite Connect status (the login/callback dance lives at root-level
# /kite/login and /kite/callback in main.py — see there for why)
# ---------------------------------------------------------------------------
@router.get("/kite/status")
async def get_kite_status():
    return {
        "has_api_key": kite_service.has_api_key,
        "connected": kite_service.is_configured,
        "login_url": "/kite/login" if kite_service.has_api_key else None,
    }


# ---------------------------------------------------------------------------
# Settings (auto-refresh cadence, position sizing, read-only adaptive weights)
# ---------------------------------------------------------------------------
@router.get("/settings", response_model=AppSettingsResponse)
async def get_settings(db: Session = Depends(get_db)):
    cfg = ranking_service.get_or_create_settings(db)
    return AppSettingsResponse.model_validate(cfg)


@router.put("/settings/refresh-interval", response_model=AppSettingsResponse)
async def set_refresh_interval(payload: RefreshIntervalUpdateRequest, db: Session = Depends(get_db)):
    """Background re-rank cadence in seconds; {"seconds": null} disables auto-refresh ("never")."""
    cfg = ranking_service.update_refresh_interval(db, payload.seconds)
    return AppSettingsResponse.model_validate(cfg)


@router.put("/settings/risk", response_model=AppSettingsResponse)
async def set_risk_settings(payload: RiskSettingsUpdateRequest, db: Session = Depends(get_db)):
    """Capital and % risked per trade, used for position sizing in trade plans."""
    cfg = ranking_service.update_risk_settings(db, payload.capital, payload.risk_per_trade_pct)
    return AppSettingsResponse.model_validate(cfg)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@router.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket):
    await connection_manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive; clients don't need to send anything.
            await websocket.receive_text()
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
