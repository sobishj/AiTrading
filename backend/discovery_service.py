"""
Daily discovery: picks the day's Auto list from the whole market instead of a fixed list.

    NIFTY 500 (NSE's list, refreshed weekly)
      -> real-data screen: every share scored by the ranking engine on its actual daily bars;
         shares whose data can't be trusted or traded are filtered out (too little history,
         stale/suspended data, illiquid, trade-to-trade series)
      -> AI review: the best AI_REVIEW_SHORTLIST candidates are analysed by every enabled model
         (multi-model) with claims checked against the data; a confident combined SELL vetoes a
         share for the day, and the combined probability moves its selection score (+/-10)
      -> catalyst route: up to CATALYST_MAX shares below the shortlist that have a fresh positive
         signal today (breakout, volume surge on an up day, high-delivery buying, positive AI-read
         filings/results news) are reviewed too, if the AI's boost could still put them on the list
      -> the day's Auto list: the best AUTO_LIST_SIZE, with a little stickiness so shares on
         yesterday's list aren't swapped out for noise.

Every run is stored (UniverseScreen) with filter counts and each selected share's reason. If
market data or NSE's list can't be fetched, the current Auto list is left unchanged rather
than rebuilt from partial data. Your Manual list and holdings are never touched; a share you
remove from Auto ("excluded") is never re-added by discovery.
"""
import asyncio
import csv
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd
from sqlalchemy import or_
from sqlalchemy.orm import Session

from config import settings
from models import AIConsensus, AIPrediction, Stock, UniverseScreen
from utils.logger import get_logger

logger = get_logger(__name__)

NSE_LIST_URLS = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
UNIVERSE_REFRESH_DAYS = 7
POOL_STALE_DAYS = 30           # a share NSE hasn't listed for this long leaves the pool

# Data checks (a share failing any of these is not considered, whatever its score)
MIN_BARS = 200                 # enough history for EMA-200, 52-week levels and the setup back-test
MAX_BAR_AGE_DAYS = 5           # newest daily bar older than this = stale feed or suspended share
MIN_PRICE = 20.0
LIQUIDITY_WINDOW = 20

KEEP_RANK_FACTOR = 1.4         # a share already on the list stays while it ranks within 1.4x the list size
AI_MAX_POINTS = 10.0           # selection points from the AI's combined probability (10% -> -10, 90% -> +10)
AI_VETO_CONFIDENCE = 60.0      # combined SELL at or above this evidence confidence drops a share for the day
MIN_ELIGIBLE_SHARE = 0.3       # fewer eligible than this share of the pool = data outage; keep the list
MIN_SECTOR_MEMBERS = 5         # shares a sector needs in the screen before it can be a "leading sector"
REVIEW_REUSE_MINUTES = 60
PRICE_MISMATCH_PCT = 2.0       # feed close vs NSE's official close on the same day; beyond this the data is suspect
FILING_READ_CANDIDATES = 40    # best candidates whose latest NSE filings the AI reads before the review
FILINGS_PER_SHARE = 3
MAX_FILING_READS = 60          # per run, so one busy filing day can't exhaust a paid model's daily cap      # a re-pick reuses an AI review younger than this; older ones are redone

# Catalyst route: shares below the shortlist with a fresh signal today also get the AI review,
# so a share moving on real news or real buying isn't missed because its trend score lags.
CATALYST_SHARE = 0.3           # extra reviews = 30% of the shortlist ...
CATALYST_MIN, CATALYST_MAX = 3, 10   # ... but at least 3 and at most 10 per run
CATALYST_AI_SENTIMENT = 60.0   # AI-read news/filings score that counts as a positive catalyst

ACTION_ORDER = {"BUY": 0, "WAIT": 1, "AVOID": 2}

# NSE industry -> the app's sector names (the curated 56 keep their finer sectors, e.g. Banking).
INDUSTRY_SECTORS = {
    "Financial Services": "Financials", "Capital Goods": "Capital Goods", "Healthcare": "Healthcare",
    "Automobile and Auto Components": "Auto", "Fast Moving Consumer Goods": "FMCG", "Chemicals": "Chemicals",
    "Information Technology": "IT", "Consumer Services": "Consumer Services", "Metals & Mining": "Metals",
    "Power": "Power", "Oil Gas & Consumable Fuels": "Energy", "Consumer Durables": "Consumer",
    "Services": "Services", "Realty": "Realty", "Construction": "Infrastructure",
    "Construction Materials": "Cement", "Telecommunication": "Telecom", "Textiles": "Textiles",
    "Media Entertainment & Publication": "Media", "Diversified": "Diversified",
}

_NAME_SUFFIX_RE = re.compile(r"[\s,]+(ltd\.?|limited)$", re.IGNORECASE)


def clean_name(company: str) -> str:
    """'ABB India Ltd.' -> 'ABB India' (what traders and headlines call it)."""
    return _NAME_SUFFIX_RE.sub("", (company or "").strip()).strip()


def parse_constituents(text: str) -> list[dict]:
    """NSE's index CSV -> [{symbol, name, sector, series}] (all series; callers filter)."""
    rows = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        symbol = (row.get("Symbol") or "").strip().upper()
        if not symbol or not re.fullmatch(r"[A-Z0-9&\-]{1,20}", symbol):
            continue
        industry = (row.get("Industry") or "").strip()
        rows.append({"symbol": symbol, "name": clean_name(row.get("Company Name") or symbol),
                     "sector": INDUSTRY_SECTORS.get(industry, industry or None),
                     "series": (row.get("Series") or "").strip().upper()})
    return rows


def eligibility(candles: pd.DataFrame, today: date, min_traded_value_cr: float,
                official: Optional[tuple[date, float]] = None) -> Optional[str]:
    """
    None if the share's data is fit to rank and trade, else the reason it's filtered out.
    `official` = NSE's (date, close) from the bhavcopy: the feed must agree with it on that day.
    """
    if candles is None or candles.empty or "date" not in candles.columns:
        return "no market data"
    if len(candles) < MIN_BARS:
        return "too little history"
    last = pd.Timestamp(candles["date"].iloc[-1])
    last_day = (last.tz_convert("Asia/Kolkata") if last.tzinfo else last).date()
    if (today - last_day).days > MAX_BAR_AGE_DAYS:
        return "stale or suspended"
    close = float(candles["close"].iloc[-1])
    if close < MIN_PRICE:
        return "price below Rs 20"
    recent = candles.tail(LIQUIDITY_WINDOW)
    traded_cr = float((recent["close"] * recent["volume"]).median()) / 1e7
    if traded_cr < min_traded_value_cr:
        return "illiquid"
    if official is not None:
        day, nse_close = official
        days = pd.to_datetime(candles["date"])
        days = (days.dt.tz_convert("Asia/Kolkata") if days.dt.tz is not None else days).dt.date
        match = candles.loc[days == day, "close"]
        if not match.empty and nse_close > 0 and abs(float(match.iloc[-1]) / nse_close - 1) * 100 > PRICE_MISMATCH_PCT:
            return "price disagrees with NSE"
    return None


def ai_points(probability_up: Optional[float]) -> float:
    if probability_up is None:
        return 0.0
    return round(max(-AI_MAX_POINTS, min(AI_MAX_POINTS, AI_MAX_POINTS * (probability_up - 50.0) / 40.0)), 2)


def is_vetoed(review: Optional[dict]) -> bool:
    return bool(review and review.get("signal") == "SELL"
                and (review.get("evidence_confidence") or 0) >= AI_VETO_CONFIDENCE)


def catalyst_reasons(item) -> list[str]:
    """Today's positive signals on a scored share (empty = nothing new happened to it today)."""
    t, s = item.technical, item.sentiment or {}
    reasons = []
    if t.breakout:
        reasons.append("broke above its 20-day high on heavy volume")
    elif t.volume_spike and (t.change_pct or 0) > 0:
        reasons.append(f"up {t.change_pct:.1f}% on {t.volume_ratio:.1f}x its average volume")
    if (item.adjustments or {}).get("delivery", 0) > 0:
        reasons.append("high-delivery buying")
    if s.get("source") == "ai" and (s.get("score") or 0) >= CATALYST_AI_SENTIMENT:
        reasons.append("AI read positive filings/news")
    elif s.get("earnings_news") and (s.get("score") or 0) > 55:
        reasons.append("positive results news")
    return reasons


def catalyst_count(shortlist: int) -> int:
    return 0 if shortlist <= 0 else max(CATALYST_MIN, min(CATALYST_MAX, round(shortlist * CATALYST_SHARE)))


def pick_catalysts(ordered: list, skip: set[str], size: int, limit: int) -> list[tuple]:
    """
    Up to `limit` (item, reasons) from best-first `ordered` (not in `skip`, not AVOID) that have a
    catalyst today AND could still make the day's list: even the full AI boost can't lift a share
    past the list's cut-off if it trails by more than 2 x AI_MAX_POINTS (the cut-off share may also
    lose up to AI_MAX_POINTS), so reviewing those would cost model calls without changing the list.
    Strongest first: most signals, then volume surge, then data score.
    """
    if limit <= 0:
        return []
    live = [i for i in ordered if i.action != "AVOID"]
    cut = live[size - 1] if len(live) >= size else None

    def reachable(item) -> bool:
        if cut is None:
            return True
        a, c = ACTION_ORDER.get(item.action, 3), ACTION_ORDER.get(cut.action, 3)
        return a < c or (a == c and item.conviction_score + 2 * AI_MAX_POINTS > cut.conviction_score)

    found = [(i, r) for i in live if i.symbol not in skip and reachable(i) for r in [catalyst_reasons(i)] if r]
    found.sort(key=lambda p: (-len(p[1]), -(p[0].technical.volume_ratio or 0), -p[0].conviction_score, p[0].symbol))
    return found[:limit]


def select(ordered: list[str], incumbents: set[str], size: int, keep_rank: int) -> list[str]:
    """
    Today's list from symbols in best-first order: shares already on the list stay while they rank
    within `keep_rank`, then the best newcomers fill the remaining places.
    """
    kept = [s for s in ordered[:keep_rank] if s in incumbents][:size]
    chosen = set(kept)
    for s in ordered:
        if len(chosen) >= size:
            break
        chosen.add(s)
    return [s for s in ordered if s in chosen]


def today_ist() -> date:
    from ai_analyst_service import today_ist as _today
    return _today()


class DiscoveryService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._top_sectors: tuple[Optional[date], list[str]] = (None, [])
        self._sectors_loaded = False

    @property
    def running(self) -> bool:
        return self._lock.locked()

    # ------------------------------------------------------------------
    # Market pool
    # ------------------------------------------------------------------
    async def fetch_constituents(self) -> Optional[list[dict]]:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=20, follow_redirects=True) as client:
            for url in NSE_LIST_URLS:
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        rows = parse_constituents(response.text)
                        if len(rows) >= 400:          # a truncated/garbled file is not the index
                            return rows
                        logger.warning("NIFTY 500 list from %s had only %d rows", url, len(rows))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("NIFTY 500 list download failed (%s): %s", url, exc)
        return None

    async def refresh_universe(self, db: Session, force: bool = False) -> dict:
        """Weekly: add NSE's current NIFTY 500 shares to the pool as candidates (EQ series only)."""
        latest = db.query(Stock.universe_seen_at).filter(Stock.universe_seen_at.isnot(None)) \
            .order_by(Stock.universe_seen_at.desc()).first()
        if latest and not force and datetime.utcnow() - latest[0] < timedelta(days=UNIVERSE_REFRESH_DAYS):
            return {"skipped": True}
        rows = await self.fetch_constituents()
        if rows is None:
            return {"error": "NSE's NIFTY 500 list could not be downloaded; the current pool is used"}
        now = datetime.utcnow()
        existing = {s.symbol: s for s in db.query(Stock).filter(Stock.symbol.in_([r["symbol"] for r in rows]))}
        added = 0
        for r in rows:
            if r["series"] != "EQ":        # BE/RR trade-to-trade: no intraday netting, not for swing trades
                continue
            stock = existing.get(r["symbol"])
            if stock is None:
                terms = {r["name"]} | ({r["symbol"]} if len(r["symbol"]) >= 3 else set())
                db.add(Stock(symbol=r["symbol"], name=r["name"], sector=r["sector"], instrument_type="equity",
                             watchlist_status="candidate", keywords=",".join(sorted(terms)), universe_seen_at=now))
                added += 1
                continue
            stock.universe_seen_at = now
            if stock.name == stock.symbol:
                stock.name = r["name"]
            stock.sector = stock.sector or r["sector"]
            if not stock.keywords:
                stock.keywords = ",".join(sorted({stock.name, stock.symbol}))
            if stock.watchlist_status == "inactive":
                stock.watchlist_status = "candidate"
        db.commit()
        logger.info("Market pool refreshed from NIFTY 500: %d shares (%d new)", len(rows), added)
        return {"constituents": len(rows), "added": added}

    @staticmethod
    def pool(db: Session) -> list[Stock]:
        cutoff = datetime.utcnow() - timedelta(days=POOL_STALE_DAYS)
        return (db.query(Stock)
                .filter(Stock.instrument_type == "equity", Stock.watchlist_status != "excluded",
                        or_(Stock.universe_seen_at >= cutoff, Stock.watchlist_status.in_(("active", "candidate"))))
                .all())

    # ------------------------------------------------------------------
    # Screen + AI review + selection
    # ------------------------------------------------------------------
    async def run(self, db: Session, trigger: str = "scheduled", ai_review: bool = True) -> dict:
        if self._lock.locked():
            return {"skipped": True, "reason": "a discovery run is already in progress"}
        async with self._lock:
            return await self._run(db, trigger, ai_review)

    async def _run(self, db: Session, trigger: str, ai_review: bool) -> dict:
        from market_service import market_service
        from ranking_service import ranking_service

        from nse_service import nse_service

        from preferences import general

        prefs = general(db)
        started = datetime.utcnow()
        universe = await self.refresh_universe(db)
        await nse_service.refresh(force=True)
        await nse_service.ensure_bhavcopy(db)
        stocks = self.pool(db)
        context = await ranking_service._build_context(db)

        candles = await asyncio.gather(*(market_service.get_candles_for_symbol(s.symbol, days=settings.HISTORY_DAYS)
                                         for s in stocks), return_exceptions=True)
        today = today_ist()
        filtered: dict[str, int] = {}
        eligible_pairs = []
        for stock, frame in zip(stocks, candles):
            reason = "no market data" if isinstance(frame, Exception) else \
                eligibility(frame, today, prefs["min_traded_value_cr"], nse_service.official_close(stock.symbol))
            if reason:
                filtered[reason] = filtered.get(reason, 0) + 1
            else:
                eligible_pairs.append((stock, frame))
        items = list(await asyncio.gather(*(asyncio.to_thread(ranking_service.score_candles, s, f, None, context)
                                             for s, f in eligible_pairs)))
        items = [i for i in items if i.technical.has_data]

        screen = UniverseScreen(trigger=trigger, pool_size=len(stocks), eligible=len(items),
                                filtered_json=json.dumps(filtered))
        if len(items) < max(prefs["auto_list_size"], MIN_ELIGIBLE_SHARE * len(stocks)):
            screen.note = (f"Only {len(items)} of {len(stocks)} shares had usable data — market data looks "
                           f"unavailable, so the Auto list was left unchanged.")
            return self._finish(db, screen, started, universe)

        # Leading sectors measured across the whole market, then each share re-tagged with them.
        # Market-wide, a sector of two or three shares is noise, not rotation.
        context.top_sectors = ranking_service._leading_sectors(items, min_members=MIN_SECTOR_MEMBERS)
        for item in items:
            item.tags.clear()
            ranking_service._apply_strategy_and_evidence(item, context)
        self._top_sectors = (today, list(context.top_sectors))
        screen.top_sectors = ",".join(context.top_sectors) or None

        items.sort(key=lambda r: (ACTION_ORDER.get(r.action, 3), -r.conviction_score, r.symbol))
        size = prefs["auto_list_size"]
        review_size = prefs["ai_review_shortlist"]
        extra = catalyst_count(review_size)
        reviews: dict[str, dict] = {}
        catalysts: list[tuple] = []
        if ai_review and settings.AI_ANALYST_ENABLED:
            # Catalyst shares get their filings read too, so a news-driven move is judged on the news.
            read_anyway = {i.symbol for i in [i for i in items if i.action != "AVOID"][:FILING_READ_CANDIDATES]}
            early = [i for i, _ in pick_catalysts(items, read_anyway, size, extra)]
            items, screen.filings_read = await self._read_filings(db, items, eligible_pairs, context, early)
            shortlist = [i for i in items if i.action != "AVOID"][:review_size]
            catalysts = pick_catalysts(items, {i.symbol for i in shortlist}, size, extra)
            reviews = await self._ai_review(db, shortlist + [i for i, _ in catalysts])
        screen.ai_reviewed = len(reviews)
        screen.catalysts_json = json.dumps([{"symbol": i.symbol, "reasons": r, "reviewed": i.symbol in reviews}
                                            for i, r in catalysts])
        catalyst_why = {i.symbol: r for i, r in catalysts}
        if ai_review and not reviews:
            screen.note = "No AI model answered, so today's list is chosen from the real-data screen alone."

        def selection_score(item) -> float:
            return item.conviction_score + ai_points((reviews.get(item.symbol) or {}).get("probability_up"))

        vetoed = {s for s, r in reviews.items() if is_vetoed(r)}
        ordered = sorted((i for i in items if i.symbol not in vetoed),
                         key=lambda r: (ACTION_ORDER.get(r.action, 3), -selection_score(r), r.symbol))
        incumbents = {s.symbol for s in stocks if s.watchlist_status == "active"}
        chosen = select([i.symbol for i in ordered], incumbents, size, int(size * KEEP_RANK_FACTOR))
        by_symbol = {i.symbol: i for i in items}

        chosen_set = set(chosen)
        for stock in stocks:
            if stock.symbol in chosen_set:
                stock.watchlist_status = "active"
            elif stock.watchlist_status == "active":
                stock.watchlist_status = "candidate"
        added = sorted(chosen_set - incumbents)
        removed = sorted(incumbents - chosen_set)

        def why(symbol: str) -> dict:
            item, review = by_symbol[symbol], reviews.get(symbol)
            entry = {"symbol": symbol, "name": item.name, "sector": item.sector, "action": item.action,
                     "data_score": item.conviction_score, "strategy": item.strategy,
                     "selection_score": round(selection_score(item), 2)}
            if review:
                entry["ai"] = {"signal": review.get("signal"), "probability_up": review.get("probability_up"),
                               "confidence": review.get("evidence_confidence"), "votes": review.get("vote_text")}
            if symbol in catalyst_why:
                entry["catalyst"] = catalyst_why[symbol]
            return entry

        screen.selected_count = len(chosen)
        screen.selected_json = json.dumps([why(s) for s in chosen])
        screen.added_json = json.dumps(added)
        screen.removed_json = json.dumps(
            [{"symbol": s, "reason": "AI review: combined SELL" if s in vetoed else
              "filtered by data checks" if s not in by_symbol else "ranked below today's best"} for s in removed])
        return self._finish(db, screen, started, universe, vetoed=sorted(vetoed))

    async def _read_filings(self, db: Session, items: list, pairs: list, context,
                            catalysts: Optional[list] = None) -> tuple[list, int]:
        """
        The AI reads the latest material NSE filings of the best candidates (once each, ever), and
        those candidates are re-scored with its reads replacing the rule-based filing rating.
        Catalyst shares are read first: for a share moving today, the filing is usually the story.
        """
        from ai_analyst_service import ai_analyst_service
        from nse_service import nse_service
        from ranking_service import ranking_service

        frames = {stock.symbol: (stock, frame) for stock, frame in pairs}
        catalysts = catalysts or []
        seen = {i.symbol for i in catalysts}
        candidates = catalysts + [i for i in items if i.action != "AVOID"
                                  and i.symbol not in seen][:FILING_READ_CANDIDATES]
        to_read = [(frames[i.symbol][0], f) for i in candidates if i.symbol in frames
                   for f in nse_service.filings_for(i.symbol)[:FILINGS_PER_SHARE]][:MAX_FILING_READS]
        if not to_read:
            return items, 0
        stored = await ai_analyst_service.analyze_news(db, to_read)
        if stored:
            context.ai_news = ai_analyst_service.news_sentiment_map(db)
            rescored = await asyncio.gather(*(asyncio.to_thread(ranking_service.score_candles, *frames[i.symbol],
                                                                None, context) for i in candidates))
            by_symbol = {r.symbol: r for r in rescored}
            items = [by_symbol.get(i.symbol, i) for i in items]
            items.sort(key=lambda r: (ACTION_ORDER.get(r.action, 3), -r.conviction_score, r.symbol))
        logger.info("AI read %d NSE filing(s) of the top %d candidates", stored, len(candidates))
        return items, stored

    async def _ai_review(self, db: Session, shortlist: list) -> dict[str, dict]:
        """
        Every enabled model analyses each shortlisted share on current data. A review made in the
        last REVIEW_REUSE_MINUTES is reused, so pressing "re-pick" twice doesn't pay twice.
        """
        from ai_analyst_service import today_ist as _today
        from ai_orchestrator import ai_orchestrator

        reviews: dict[str, dict] = {}
        for item in shortlist:
            try:
                done = (db.query(AIPrediction)
                        .filter(AIPrediction.stock_id == item.stock_id, AIPrediction.prediction_date == _today(),
                                AIPrediction.kind == "live", AIPrediction.role == "primary",
                                AIPrediction.model == "consensus", AIPrediction.consensus_id.isnot(None)).first())
                consensus = db.get(AIConsensus, done.consensus_id) if done else None
                if consensus is not None and consensus.created_at < datetime.utcnow() - timedelta(
                        minutes=REVIEW_REUSE_MINUTES):
                    consensus = None
                result = (ai_orchestrator.payload(db, consensus) if consensus is not None
                          else await ai_orchestrator.analyze(db, item, trigger="daily", force=True))
            except Exception as exc:  # noqa: BLE001  (one share's failure must not stop the review)
                logger.error("AI review of %s failed: %s", item.symbol, exc)
                db.rollback()
                continue
            if result and result.get("available"):
                reviews[item.symbol] = result
        logger.info("AI review: %d of %d shortlisted shares analysed", len(reviews), len(shortlist))
        return reviews

    def _finish(self, db: Session, screen: UniverseScreen, started: datetime, universe: dict,
                vetoed: Optional[list[str]] = None) -> dict:
        if universe.get("error"):
            screen.note = ((screen.note + " ") if screen.note else "") + universe["error"] + "."
        screen.seconds = round((datetime.utcnow() - started).total_seconds(), 1)
        db.add(screen)
        db.commit()
        logger.info("Discovery: pool %d, eligible %d, AI-reviewed %d, selected %d (+%d/-%d) in %.0fs%s",
                    screen.pool_size, screen.eligible, screen.ai_reviewed, screen.selected_count,
                    len(json.loads(screen.added_json or "[]")), len(json.loads(screen.removed_json or "[]")),
                    float(screen.seconds), f" — {screen.note}" if screen.note else "")
        return {**self.screen_payload(screen), "vetoed": vetoed or []}

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def market_top_sectors(self) -> Optional[list[str]]:
        """Today's leading sectors from the market-wide screen (None if no screen ran today)."""
        if not self._sectors_loaded:
            self._sectors_loaded = True
            try:
                from database import db_session
                with db_session() as db:
                    latest = db.query(UniverseScreen).filter(UniverseScreen.selected_count > 0) \
                        .order_by(UniverseScreen.run_at.desc()).first()
                    if latest is not None:
                        from ai_analyst_service import IST
                        day = latest.run_at.replace(tzinfo=timezone.utc).astimezone(IST).date()
                        self._top_sectors = (day, (latest.top_sectors or "").split(",") if latest.top_sectors else [])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load today's screen: %s", exc)
        day, sectors = self._top_sectors
        return sectors if day == today_ist() and sectors else None

    @staticmethod
    def screen_payload(screen: Optional[UniverseScreen]) -> dict:
        if screen is None:
            return {"available": False}
        return {"available": True, "id": screen.id, "run_at": screen.run_at.isoformat() + "Z",
                "trigger": screen.trigger, "pool_size": screen.pool_size, "eligible": screen.eligible,
                "ai_reviewed": screen.ai_reviewed, "selected_count": screen.selected_count,
                "filtered": json.loads(screen.filtered_json or "{}"),
                "selected": json.loads(screen.selected_json or "[]"),
                "added": json.loads(screen.added_json or "[]"),
                "removed": json.loads(screen.removed_json or "[]"),
                "top_sectors": screen.top_sectors.split(",") if screen.top_sectors else [],
                "filings_read": screen.filings_read or 0,
                "catalysts": json.loads(screen.catalysts_json or "[]"),
                "note": screen.note, "seconds": float(screen.seconds) if screen.seconds is not None else None}

    def status(self, db: Session) -> dict:
        latest = db.query(UniverseScreen).order_by(UniverseScreen.run_at.desc()).first()
        from nse_service import nse_service
        from preferences import general
        return {**self.screen_payload(latest), "running": self.running, "sources": nse_service.status(),
                "auto_list_size": general(db)["auto_list_size"], "ai_review_shortlist": general(db)["ai_review_shortlist"],
                "pool_now": len(self.pool(db))}

    def ran_today(self, db: Session) -> bool:
        from ai_analyst_service import IST
        start = datetime.combine(today_ist(), datetime.min.time(), IST).astimezone(
            timezone.utc).replace(tzinfo=None)
        return db.query(UniverseScreen.id).filter(UniverseScreen.run_at >= start,
                                                   UniverseScreen.selected_count > 0).first() is not None


discovery_service = DiscoveryService()
