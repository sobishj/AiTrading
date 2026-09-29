"""
AiTrading FastAPI application entry point.

Wires up CORS, the API router (incl. WebSocket updates feed), global error
handling, and the always-on background engine (PRD §11, §13, §18):

- ranking loop: re-ranks the watchlist on the user-configurable cadence and
  records the top BUY ideas as recommendations;
- news watch: scans the news feeds every few minutes and re-ranks immediately
  when a new headline mentions a watchlist stock;
- scheduler: the Morning Brief at MORNING_BRIEF_TIME, the AI analyst's daily
  forecasts at AI_FORECAST_TIME, and the learning cycle (grading, weight
  recalibration, then the AI analyst's reflection) at POST_MARKET_LEARNING_TIME,
  IST, weekdays;
- AI analyst: Qwen reads every new headline about a tracked stock as it arrives,
  practises on historical charts whenever the market is closed, and writes a
  next-session market outlook each evening and pre-market (see ai_analyst_service).

Every change is broadcast to connected clients over WebSocket.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, time as dtime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ai_analyst_service import (
    INTERACTIVE_COOLDOWN_SECONDS, PRACTICE_BATCH, PRACTICE_MAX_PER_DAY, ai_analyst_service, today_ist,
)
from api.ai_routes import router as ai_router
from api.llm_routes import router as llm_router
from api.portfolio_routes import router as portfolio_router
from api.routes import connection_manager, refresh_and_broadcast, router
from brief_service import brief_service
from config import settings
from data_provider import yahoo_provider
from database import db_session, init_db
from kite_service import kite_service
from learning_service import learning_service
from llm_service import llm_service
from market_service import keyword_pattern, market_service
from models import Stock, tracked_stock_filter
from position_service import position_service
from ranking_service import ranking_service
from utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

_background_tasks: list[asyncio.Task] = []


# ---------------------------------------------------------------------------
# AI analyst jobs (run one at a time on the LLM worker queue)
# ---------------------------------------------------------------------------
def enqueue_ai_news(pairs: list) -> None:
    """Queue Qwen reads for (stock, headline) pairs; stocks are re-loaded inside the job's own session."""
    if not settings.AI_ANALYST_ENABLED or not pairs:
        return
    items = [(stock.id, item) for stock, item in pairs]

    async def job() -> None:
        with db_session() as db:
            stocks = {s.id: s for s in db.query(Stock).filter(Stock.id.in_({sid for sid, _ in items})).all()}
            stored = await ai_analyst_service.analyze_news(db, [(stocks[sid], item) for sid, item in items if sid in stocks])
            if stored:
                await refresh_and_broadcast(db, trigger=f"AI analyst read {stored} new headline(s)")

    llm_service.enqueue(job, label=f"ai-news:{len(items)}")


def enqueue_ai_forecasts(force: bool = False) -> None:
    if not settings.AI_ANALYST_ENABLED:
        return

    async def job() -> None:
        with db_session() as db:
            ranked = await ranking_service.run_full_ranking(db, max_age=600)
            made = await ai_analyst_service.make_daily_predictions(db, ranked, force=force)
            if made:
                await refresh_and_broadcast(db, trigger=f"AI analyst made {made} forecast(s)")
                await connection_manager.broadcast({"type": "ai_update", "forecasts": made})

    llm_service.enqueue(job, label="ai-forecasts")


def enqueue_ai_outlook() -> None:
    if not settings.AI_ANALYST_ENABLED:
        return

    async def job() -> None:
        with db_session() as db:
            await ai_analyst_service.grade_outlooks(db)
            outlook = await ai_analyst_service.make_market_outlook(db)
            if outlook is not None:
                await connection_manager.broadcast(
                    {"type": "ai_outlook", **ai_analyst_service.outlook_payload(outlook)})

    llm_service.enqueue(job, label="ai-outlook")


def market_is_open(now: datetime | None = None) -> bool:
    """NSE cash session (with a little slack), weekdays; holidays are not modelled."""
    now = now or datetime.now(IST)
    return now.weekday() < 5 and dtime(9, 0) <= now.time() <= dtime(15, 45)


async def _practice_loop() -> None:
    """
    While the market is closed, keep Qwen practising on historical charts —
    one small batch at a time, only when the LLM is otherwise idle and the user
    hasn't chatted in the last couple of minutes, up to a daily cap.
    """
    while True:
        try:
            await asyncio.sleep(30)
            if (not settings.AI_ANALYST_ENABLED or market_is_open() or not llm_service.idle
                    or llm_service.seconds_since_interactive() < INTERACTIVE_COOLDOWN_SECONDS
                    or not llm_service.practice_allowed()
                    or not await llm_service.is_available("background")):
                continue
            with db_session() as db:
                if (not ai_analyst_service.practice_enabled(db)
                        or ai_analyst_service.practice_done_today(db) >= PRACTICE_MAX_PER_DAY):
                    continue

            async def job() -> None:
                with db_session() as db:
                    done = await ai_analyst_service.run_practice_batch(db, PRACTICE_BATCH)
                    if done:
                        lesson = await ai_analyst_service.reflect(db)
                        if lesson is not None:
                            await connection_manager.broadcast({"type": "ai_update", "lessons_version": lesson.version})

            llm_service.enqueue(job, label="ai-practice")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Practice loop error: %s", exc, exc_info=True)


async def run_position_monitor() -> None:
    """Assess every holding once and push any new alerts (app + Windows notification)."""
    with db_session() as db:
        alerts = await position_service.monitor(db)
        if alerts:
            await connection_manager.broadcast({"type": "position_alerts",
                                                "alerts": [position_service.alert_payload(a) for a in alerts]})
            await position_service.notify(db, alerts)


async def _position_monitor_loop() -> None:
    """Holdings are checked every minute while the market is open, every 30 minutes otherwise."""
    last_run: datetime | None = None
    while True:
        try:
            await asyncio.sleep(20)
            interval = 60 if market_is_open() else 1800
            if last_run is None or (datetime.utcnow() - last_run).total_seconds() >= interval:
                await run_position_monitor()
                last_run = datetime.utcnow()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Position monitor error: %s", exc, exc_info=True)


async def run_factor_study(force: bool = False, delay: float = 0) -> None:
    """Measure factor success rates on past daily bars (statistics, no LLM; refreshed weekly)."""
    try:
        if delay:
            await asyncio.sleep(delay)
        from knowledge_service import knowledge_service
        with db_session() as db:
            result = await knowledge_service.history_study(db, force=force)
        if not result.get("skipped"):
            await connection_manager.broadcast({"type": "learning_update", "factor_study": result})
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Factor study failed: %s", exc, exc_info=True)


def enqueue_ai_reflection(force: bool = False) -> None:
    if not settings.AI_ANALYST_ENABLED:
        return

    async def job() -> None:
        with db_session() as db:
            lesson = await ai_analyst_service.reflect(db, force=force)
            if lesson is not None:
                await connection_manager.broadcast({"type": "ai_update", "lessons_version": lesson.version})

    llm_service.enqueue(job, label="ai-reflection")

# How often the ranking loop checks the current (possibly just-changed)
# refresh setting — NOT the refresh cadence itself.
_SETTINGS_POLL_SECONDS = 5
_SCHEDULER_POLL_SECONDS = 30


async def _ranking_refresh_loop() -> None:
    """
    Periodically re-rank the watchlist and broadcast, honoring the persisted
    refresh interval (AppSettings.ranking_refresh_seconds). A null interval
    means "never": only /api/refresh, news triggers and the brief re-rank.
    """
    last_run: datetime | None = None

    while True:
        try:
            await asyncio.sleep(_SETTINGS_POLL_SECONDS)

            with db_session() as db:
                interval = ranking_service.get_or_create_settings(db).ranking_refresh_seconds

            if interval is None:
                continue  # auto-refresh disabled

            due = last_run is None or (datetime.utcnow() - last_run).total_seconds() >= interval
            if not due:
                continue

            with db_session() as db:
                await refresh_and_broadcast(db, trigger="scheduled")
            last_run = datetime.utcnow()
            logger.info("Background ranking refresh (interval=%ss) broadcast to %d clients",
                        interval, len(connection_manager.active_connections))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Ranking refresh loop error: %s", exc, exc_info=True)


async def _news_watch_loop() -> None:
    """
    PRD §11 "new company news → re-analyze": poll the feeds and, when a fresh
    headline mentions a watchlist stock, re-rank at once and tell the UI why.
    """
    seen: set[str] | None = None
    while True:
        try:
            news = await market_service.fetch_news_async(force_refresh=True)
            titles = {item["title"] for item in news}
            if seen is None:
                seen = titles  # first pass: baseline, don't treat the backlog as new
            else:
                fresh = [item for item in news if item["title"] not in seen]
                seen |= titles
                if fresh:
                    with db_session() as db:
                        stocks = db.query(Stock).filter(tracked_stock_filter()).all()
                        enqueue_ai_news(ai_analyst_service.match_news(stocks, fresh))
                        hits = []
                        for stock in stocks:
                            terms = [k for k in (stock.keywords or stock.name).split(",") if k.strip()]
                            pattern = keyword_pattern(terms)
                            for item in fresh:
                                if pattern and pattern.search(item["title"] + " " + item["summary"]):
                                    hits.append((stock, item))
                                    break
                        if hits:
                            names = ", ".join(stock.name for stock, _ in hits[:3])
                            trigger = f"News on {names}: {hits[0][1]['title']}"
                            logger.info("News trigger: %s", trigger)
                            await refresh_and_broadcast(db, trigger=trigger)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("News watch loop error: %s", exc, exc_info=True)
        await asyncio.sleep(max(60, settings.NEWS_SCAN_INTERVAL))


def _parse_hhmm(value: str, default: dtime) -> dtime:
    try:
        hours, minutes = value.split(":")
        return dtime(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return default


async def _daily_scheduler_loop() -> None:
    """Morning Brief and post-market learning, once per weekday each (IST)."""
    brief_at = _parse_hhmm(settings.MORNING_BRIEF_TIME, dtime(8, 30))
    learn_at = _parse_hhmm(settings.POST_MARKET_LEARNING_TIME, dtime(16, 0))
    forecast_at = _parse_hhmm(settings.AI_FORECAST_TIME, dtime(8, 45))
    last_brief_date = None
    last_learning_date = None
    last_forecast_date = None
    outlook_times = [_parse_hhmm(t.strip(), dtime(20, 0)) for t in settings.AI_OUTLOOK_TIMES.split(",") if t.strip()]
    last_outlook_run: set[tuple] = set()

    while True:
        try:
            now = datetime.now(IST)
            for at in outlook_times:
                key = (now.date(), at)
                # Within 10 minutes after each slot, once per slot per day (any day: it targets the next session).
                start = datetime.combine(now.date(), at, IST)
                if key not in last_outlook_run and start <= now <= start + timedelta(minutes=10):
                    enqueue_ai_outlook()
                    last_outlook_run.add(key)
            if now.weekday() < 5:
                if last_brief_date != now.date() and now.time() >= brief_at:
                    with db_session() as db:
                        existing = brief_service.get_today(db)
                        # A brief generated on demand before the scheduled time
                        # is refreshed with pre-open data.
                        generated_ist = (existing.timestamp.replace(tzinfo=timezone.utc).astimezone(IST).time()
                                         if existing is not None else None)
                        if generated_ist is None or generated_ist < brief_at:
                            brief = await brief_service.generate(db, force=True)
                            await connection_manager.broadcast(
                                {"type": "morning_brief", **brief_service.to_payload(brief)})
                    last_brief_date = now.date()
                if last_forecast_date != now.date() and now.time() >= forecast_at:
                    # Idempotent: stocks already forecast today are skipped.
                    enqueue_ai_forecasts()
                    last_forecast_date = now.date()
                if last_learning_date != now.date() and now.time() >= learn_at:
                    with db_session() as db:
                        result = await learning_service.run_learning_cycle(db)
                    logger.info("Post-market learning: graded=%s ai_forecasts_graded=%s recalibration=%s",
                                result["graded"], result["ai_forecasts_graded"], result["recalibration"]["status"])
                    await connection_manager.broadcast({"type": "learning_update", "graded": result["graded"]})
                    enqueue_ai_reflection()
                    asyncio.create_task(run_factor_study())
                    last_learning_date = now.date()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Daily scheduler error: %s", exc, exc_info=True)
        await asyncio.sleep(_SCHEDULER_POLL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s environment), market data via %s",
                settings.APP_NAME, settings.APP_ENV, market_service.data_source)
    init_db()
    try:
        from credential_store import migrate_plaintext_keys
        migrate_plaintext_keys()   # any API key still in the database moves to Windows Credential Manager
    except Exception as exc:  # noqa: BLE001
        logger.warning("API-key migration skipped: %s", exc.__class__.__name__)
    llm_service.reload_profiles()
    llm_service.start_worker()
    if settings.AI_ANALYST_ENABLED:
        news = await market_service.fetch_news_async()
        with db_session() as db:
            stocks = db.query(Stock).filter(tracked_stock_filter()).all()
            enqueue_ai_news(ai_analyst_service.match_news(stocks, news))
    if settings.AI_ANALYST_ENABLED:
        with db_session() as db:
            latest = ai_analyst_service.latest_outlook(db)
            if latest is None or latest.session_date < ai_analyst_service.next_session_date():
                enqueue_ai_outlook()
    _background_tasks.extend([
        asyncio.create_task(_practice_loop()),
        asyncio.create_task(_position_monitor_loop()),
        asyncio.create_task(_ranking_refresh_loop()),
        asyncio.create_task(_news_watch_loop()),
        asyncio.create_task(_daily_scheduler_loop()),
        asyncio.create_task(run_factor_study(delay=120)),   # first run / weekly refresh, after startup settles
    ])
    yield
    logger.info("Shutting down %s", settings.APP_NAME)
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    await llm_service.stop_worker()
    await yahoo_provider.close()


app = FastAPI(
    title="AiTrading API",
    description="AI-powered NSE market analysis and trade recommendation engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "path": str(request.url.path)},
    )


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "timestamp": datetime.utcnow().isoformat(),
    }


app.include_router(router, prefix="/api")
app.include_router(portfolio_router, prefix="/api")
app.include_router(llm_router, prefix="/api")
app.include_router(ai_router, prefix="/api")


# ---------------------------------------------------------------------------
# Zerodha Kite Connect OAuth flow.
#
# Kite's login flow is a browser redirect dance, not a JSON API call, so
# these live at the exact root-level path Zerodha redirects back to
# (KITE_REDIRECT_URL=http://localhost:8000/kite/callback) rather than under
# /api. Access tokens are Kite-issued and expire daily by design — there is
# no way around re-completing this flow once every trading day.
# ---------------------------------------------------------------------------
@app.get("/kite/login")
async def kite_login():
    """Redirect the browser into Zerodha's login page."""
    if not kite_service.has_api_key:
        return HTMLResponse(
            "<h3>Kite API key not configured</h3>"
            "<p>Set KITE_API_KEY and KITE_API_SECRET in backend/.env, then restart the backend.</p>",
            status_code=400,
        )
    login_url = kite_service.get_login_url()
    return RedirectResponse(login_url)


@app.get("/kite/callback")
async def kite_callback(request: Request):
    """
    Zerodha redirects here after a successful login with a one-time
    request_token in the query string. Exchange it for an access_token,
    then send the user back to the app.
    """
    request_token = request.query_params.get("request_token")
    status = request.query_params.get("status")

    if not request_token or status != "success":
        logger.warning("Kite callback missing request_token or non-success status: %s", dict(request.query_params))
        return HTMLResponse(
            f"<h3>Zerodha login did not complete</h3><p>status={status}</p>"
            f"<p><a href='{settings.FRONTEND_URL}'>Return to AiTrading</a></p>",
            status_code=400,
        )

    try:
        kite_service.generate_session(request_token)
    except Exception as exc:  # noqa: BLE001
        logger.error("Kite session exchange failed: %s", exc)
        return HTMLResponse(
            f"<h3>Zerodha login failed</h3><p>{exc}</p>"
            f"<p><a href='{settings.FRONTEND_URL}'>Return to AiTrading</a></p>",
            status_code=500,
        )

    logger.info("Kite Connect session established via OAuth callback")
    return RedirectResponse(f"{settings.FRONTEND_URL}?kite=connected")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
