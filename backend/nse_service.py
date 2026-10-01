"""
Official NSE data, alongside the price feed and news sites:

- corporate filings (announcements): results, orders, management changes, regulatory action ...
  — the primary source for company news, covering most listed shares, unlike the news sites;
- the event calendar: upcoming board meetings for financial results (a held trade can gap on them);
- the bhavcopy: NSE's official end-of-day close, volume and delivery % per share — used to
  cross-check the price feed and to see whether a move was backed by delivery buying;
- the F&O ban list (open interest above 95% of the market-wide limit) and bulk/block deals.

Everything is cached with a TTL and fetched with a browser-like client; a failed fetch keeps the
last good data and is reported in status() — never silently treated as "no news".
Filings become news-like items tied to their share by exact symbol (no keyword guessing). Routine
paperwork (trading-window closures, meeting notices, newspaper copies ...) is skipped.
"""
import asyncio
import csv
import io
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}
ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={frm}&to_date={to}"
CALENDAR_URL = "https://www.nseindia.com/api/event-calendar"
BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
DEALS_URL = "https://www.nseindia.com/api/snapshot-capital-market-largedeal"
BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"

FILINGS_LOOKBACK_DAYS = 3          # matches the 72-hour news window
FILINGS_TTL = 15 * 60
CALENDAR_TTL = 6 * 3600
BAN_TTL = 3 * 3600
DEALS_TTL = 3600
BHAV_SESSIONS = 21                 # latest session + 20 for the delivery average
BHAV_MAX_AGE_DAYS = 5              # older than this = not today's picture; delivery/cross-check unused
RESULTS_RISK_DAYS = 7              # results board meeting within this many days = gap risk for a swing trade
HISTORY_SESSIONS = 250             # a year of official records, for measuring what delivery predicts
DELIVERY_RATIO_MIN = 1.3           # "unusually high" delivery vs its 20-session average
MIN_CHANGE_PCT = 0.5               # ...on a day that actually moved
MIN_EDGE_SAMPLES = 30              # a measured edge is used only from this many cases

# Filing categories that are paperwork, not news (matched case-insensitively as substrings).
ROUTINE = (
    "trading window", "shareholders meeting", "newspaper publication", "analysts/institutional investor meet",
    "esop", "duplicate share certificate", "loss of share certificate", "company secretary",
    "committee meeting", "amendment to aoa", "spurt in volume", "reg. 74", "regulation 74", "record date",
    "book closure", "investor complaints", "compliance certificate", "certificate under sebi",
    "closure of trading window", "postal ballot", "voting results", "scrutinizer",
)
# Categories whose direction is clear from the category itself; others fall back to the wording.
CATEGORY_SENTIMENT = {
    "bagging/receiving of orders": 1, "commencement of commercial production": 1, "capacity addition": 1,
    "resignation of statutory auditor": -1, "action(s) taken or orders passed": -1,
    "action(s) initiated or orders passed": -1, "pendency of litigation": -1, "default": -1,
    "fraud": -1, "insolvency": -1,
}
_INFORMED_RE = re.compile(r"^.*?has informed the exchange\s*(?:about|regarding|that|on)?\s*", re.IGNORECASE)
_NAME_SUFFIX_RE = re.compile(r"[\s,]+(ltd\.?|limited)$", re.IGNORECASE)


def _clean_company(name: str) -> str:
    return _NAME_SUFFIX_RE.sub("", (name or "").strip()).strip()


def is_routine(category: str) -> bool:
    c = (category or "").lower()
    return any(r in c for r in ROUTINE)


def filing_sentiment(category: str, text: str) -> int:
    c = (category or "").lower()
    for key, value in CATEGORY_SENTIMENT.items():
        if key in c:
            return value
    from market_service import headline_sentiment
    return headline_sentiment(f"{category} {text}")


def parse_filing(row: dict) -> Optional[dict]:
    """One NSE announcement -> a news-like item tied to its share (None for routine paperwork)."""
    symbol = (row.get("symbol") or "").strip().upper()
    category = (row.get("desc") or "").strip()
    if not symbol or not category or is_routine(category):
        return None
    text = re.sub(r"\s+", " ", row.get("attchmntText") or "").strip()
    detail = _INFORMED_RE.sub("", text).strip(" .")
    company = _clean_company(row.get("sm_name") or symbol)
    title = f"{company}: {category}"
    if detail and detail.lower() not in category.lower() and len(detail) > 12:
        title += f" — {detail}"
    try:
        published = datetime.strptime(row.get("an_dt") or "", "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST).isoformat()
    except ValueError:
        published = ""
    return {"title": title[:260], "summary": text[:400], "published": published,
            "link": row.get("attchmntFile") or "", "source": "NSE filing", "symbol": symbol,
            "category": category, "sentiment": filing_sentiment(category, text), "market_moving": False}


def parse_calendar(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows or []:
        try:
            when = datetime.strptime(r.get("date") or "", "%d-%b-%Y").date()
        except ValueError:
            continue
        purpose = (r.get("purpose") or "").strip()
        out[(r.get("symbol") or "").strip().upper()].append({
            "date": when, "purpose": purpose,
            "results": "result" in f"{purpose} {r.get('bm_desc') or ''}".lower()})
    return out


def parse_ban(text: str) -> tuple[Optional[date], set[str]]:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    when = None
    if lines:
        m = re.search(r"(\d{2}-[A-Za-z]{3}-\d{4})", lines[0])
        if m:
            when = datetime.strptime(m.group(1).title(), "%d-%b-%Y").date()
    symbols = {l.split(",", 1)[1].strip().upper() for l in lines[1:] if "," in l}
    return when, symbols


def parse_bhavcopy(text: str) -> list[dict]:
    """sec_bhavdata_full CSV (spaces after commas, '-' for missing) -> EQ/BE rows."""
    def num(v):
        v = (v or "").strip()
        try:
            return float(v) if v not in ("", "-") else None
        except ValueError:
            return None

    rows = []
    reader = csv.DictReader(io.StringIO(text), skipinitialspace=True)
    for r in reader:
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        series = r.get("SERIES", "")
        if series not in ("EQ", "BE"):
            continue
        try:
            when = datetime.strptime(r["DATE1"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        close = num(r.get("CLOSE_PRICE"))
        if close is None:
            continue
        rows.append({"trade_date": when, "symbol": r["SYMBOL"].upper(), "series": series,
                     "prev_close": num(r.get("PREV_CLOSE")), "open": num(r.get("OPEN_PRICE")),
                     "high": num(r.get("HIGH_PRICE")), "low": num(r.get("LOW_PRICE")), "close": close,
                     "volume": int(num(r.get("TTL_TRD_QNTY")) or 0) or None,
                     "deliv_qty": int(num(r.get("DELIV_QTY")) or 0) or None, "deliv_pct": num(r.get("DELIV_PER"))})
    return rows


def parse_deals(payload: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for kind, key in (("bulk", "BULK_DEALS_DATA"), ("block", "BLOCK_DEALS_DATA")):
        for d in (payload or {}).get(key) or []:
            symbol = (d.get("symbol") or "").strip().upper()
            try:
                qty, price = float(d.get("qty") or 0), float(d.get("watp") or 0)
            except ValueError:
                continue
            if symbol and qty:
                out[symbol].append({"kind": kind, "side": (d.get("buySell") or "").upper(), "client": d.get("clientName"),
                                    "qty": qty, "price": price, "date": d.get("date")})
    return out


def delivery_view(rows: list[dict]) -> Optional[dict]:
    """Latest session's delivery vs the average of the sessions before it (rows newest first)."""
    if not rows or rows[0].get("deliv_pct") is None:
        return None
    latest, earlier = rows[0], [r["deliv_pct"] for r in rows[1:BHAV_SESSIONS] if r.get("deliv_pct") is not None]
    avg = sum(earlier) / len(earlier) if len(earlier) >= 10 else None
    change = ((latest["close"] / latest["prev_close"] - 1) * 100) if latest.get("prev_close") else None
    return {"date": latest["trade_date"], "close": latest["close"], "change_pct": change,
            "deliv_pct": latest["deliv_pct"], "deliv_avg_20": round(avg, 1) if avg is not None else None,
            "deliv_ratio": round(latest["deliv_pct"] / avg, 2) if avg else None}


class NSEService:
    def __init__(self) -> None:
        self._filings: dict[str, list[dict]] = {}
        self._calendar: dict[str, list[dict]] = {}
        self._ban: tuple[Optional[date], set[str]] = (None, set())
        self._deals: dict[str, list[dict]] = {}
        self._delivery: dict[str, dict] = {}
        self._sessions: dict[str, list[dict]] = {}      # official daily bars per share, newest first
        # Measured from a year of NSE history (knowledge_service.delivery_study); None = not measured yet.
        self.delivery_edges: dict[str, Optional[dict]] = {"delivery_buying": None, "delivery_selling": None}
        self._fetched: dict[str, float] = {}
        self._status: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------
    def _due(self, key: str, ttl: float, force: bool) -> bool:
        return force or time.monotonic() - self._fetched.get(key, 0) >= ttl

    def _ok(self, key: str, **info) -> None:
        from sources_service import sources_service
        self._fetched[key] = time.monotonic()
        self._status[key] = {"ok": True, "at": datetime.now(IST).isoformat(timespec="minutes"), **info}
        sources_service.record(f"nse_{key}", True, items=info.get("shares", info.get("sessions")))

    def _fail(self, key: str, exc: Exception) -> None:
        from sources_service import sources_service
        prev = self._status.get(key, {})
        error = f"{exc.__class__.__name__}: {str(exc)[:120]}"
        self._status[key] = {**prev, "ok": False, "error": error,
                             "failed_at": datetime.now(IST).isoformat(timespec="minutes")}
        sources_service.record(f"nse_{key}", False, error=error)
        logger.warning("NSE %s fetch failed: %s", key, exc)

    def _switched_off(self, key: str) -> bool:
        """A source switched off in Settings: drop what it provided so nothing keeps using it."""
        from sources_service import sources_service
        if sources_service.is_enabled(f"nse_{key}"):
            return False
        empty = {"filings": {}, "calendar": {}, "ban": (None, set()), "deals": {}}
        if key in empty:
            setattr(self, f"_{key}", empty[key])
        elif key == "bhavcopy":
            self._delivery, self._sessions = {}, {}
        self._status[key] = {"ok": False, "off": True}
        self._fetched.pop(key, None)       # fetch straight away when it's switched back on
        return True

    async def refresh(self, force: bool = False) -> None:
        """Refresh whatever is due (filings, calendar, ban list, deals). Never raises."""
        async with self._lock:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
                if not self._switched_off("filings") and self._due("filings", FILINGS_TTL, force):
                    try:
                        today = datetime.now(IST).date()
                        url = ANNOUNCEMENTS_URL.format(frm=(today - timedelta(days=FILINGS_LOOKBACK_DAYS)).strftime("%d-%m-%Y"),
                                                       to=today.strftime("%d-%m-%Y"))
                        response = await client.get(url)
                        response.raise_for_status()
                        by_symbol: dict[str, list[dict]] = defaultdict(list)
                        seen = set()
                        for row in response.json():
                            item = parse_filing(row)
                            if item and (item["symbol"], item["title"]) not in seen:
                                seen.add((item["symbol"], item["title"]))
                                by_symbol[item["symbol"]].append(item)
                        for items in by_symbol.values():
                            items.sort(key=lambda i: i["published"], reverse=True)
                        self._filings = dict(by_symbol)
                        self._ok("filings", shares=len(by_symbol), filings=sum(len(v) for v in by_symbol.values()))
                    except Exception as exc:  # noqa: BLE001
                        self._fail("filings", exc)
                if not self._switched_off("calendar") and self._due("calendar", CALENDAR_TTL, force):
                    try:
                        response = await client.get(CALENDAR_URL)
                        response.raise_for_status()
                        self._calendar = parse_calendar(response.json())
                        self._ok("calendar", shares=len(self._calendar))
                    except Exception as exc:  # noqa: BLE001
                        self._fail("calendar", exc)
                if not self._switched_off("ban") and self._due("ban", BAN_TTL, force):
                    try:
                        response = await client.get(BAN_URL)
                        response.raise_for_status()
                        self._ban = parse_ban(response.text)
                        self._ok("ban", date=str(self._ban[0]), shares=len(self._ban[1]))
                    except Exception as exc:  # noqa: BLE001
                        self._fail("ban", exc)
                if not self._switched_off("deals") and self._due("deals", DEALS_TTL, force):
                    try:
                        response = await client.get(DEALS_URL)
                        response.raise_for_status()
                        payload = response.json()
                        self._deals = parse_deals(payload)
                        self._ok("deals", as_on=payload.get("as_on_date"), shares=len(self._deals))
                    except Exception as exc:  # noqa: BLE001
                        self._fail("deals", exc)

    async def ensure_bhavcopy(self, db: Session) -> dict:
        """Download any missing official end-of-day files for the last BHAV_SESSIONS sessions, then load them."""
        from models import NSEDaily

        if self._switched_off("bhavcopy"):
            return {"downloaded": 0, "latest": None, "off": True}
        # Days stored before open/high/low were recorded are fetched again (they're needed to fill feed gaps).
        incomplete = {d for (d,) in db.query(NSEDaily.trade_date).filter(NSEDaily.open.is_(None),
                                                                         NSEDaily.series == "EQ").distinct()}
        if incomplete:
            db.query(NSEDaily).filter(NSEDaily.trade_date.in_(incomplete)).delete(synchronize_session=False)
            db.commit()
        have = {d for (d,) in db.query(NSEDaily.trade_date).distinct()}
        today = datetime.now(IST).date()
        added, sessions, day = 0, 0, today
        async with httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
            for _ in range(40):                      # ~40 calendar days covers 21 sessions plus holidays
                if sessions >= BHAV_SESSIONS:
                    break
                if day.weekday() < 5:
                    if day in have:
                        sessions += 1
                    elif not (day == today and datetime.now(IST).hour < 19):   # today's file comes out ~6 pm
                        try:
                            response = await client.get(BHAV_URL.format(d=day))
                            if response.status_code == 200 and "SYMBOL" in response.text[:200]:
                                rows = parse_bhavcopy(response.text)
                                # On a market holiday NSE can serve the previous session's file: trust the
                                # date inside the file, not the one asked for.
                                file_day = rows[0]["trade_date"] if rows else None
                                if file_day is not None and file_day not in have:
                                    db.bulk_insert_mappings(NSEDaily, [r for r in rows if r["trade_date"] == file_day])
                                    db.commit()
                                    have.add(file_day)
                                    added += 1
                                    sessions += 1
                        except Exception as exc:  # noqa: BLE001
                            db.rollback()
                            self._fail("bhavcopy", exc)
                            break
                day -= timedelta(days=1)
        self.load_delivery(db)
        latest = max((v["date"] for v in self._delivery.values()), default=None)
        if latest is not None:
            self._ok("bhavcopy", date=str(latest), sessions=sessions, downloaded=added)
        return {"downloaded": added, "latest": str(latest) if latest else None}

    def load_delivery(self, db: Session) -> None:
        from models import NSEDaily

        if self._switched_off("bhavcopy"):
            return
        dates = [d for (d,) in db.query(NSEDaily.trade_date).distinct().order_by(NSEDaily.trade_date.desc())
                 .limit(BHAV_SESSIONS)]
        if not dates:
            return
        rows: dict[str, list[dict]] = defaultdict(list)
        for r in (db.query(NSEDaily).filter(NSEDaily.trade_date.in_(dates))
                  .order_by(NSEDaily.symbol, NSEDaily.trade_date.desc())):
            rows[r.symbol].append({"trade_date": r.trade_date, "close": float(r.close),
                                   "open": float(r.open) if r.open is not None else None,
                                   "high": float(r.high) if r.high is not None else None,
                                   "low": float(r.low) if r.low is not None else None,
                                   "volume": int(r.volume) if r.volume is not None else None,
                                   "prev_close": float(r.prev_close) if r.prev_close is not None else None,
                                   "deliv_pct": float(r.deliv_pct) if r.deliv_pct is not None else None})
        self._sessions = dict(rows)
        self._delivery = {s: v for s, rs in rows.items() if rs[0]["trade_date"] == dates[0]
                          and (v := delivery_view(rs)) is not None}
        # Shares without delivery data still need the official close for the cross-check.
        for s, rs in rows.items():
            if s not in self._delivery and rs[0]["trade_date"] == dates[0]:
                self._delivery[s] = {"date": rs[0]["trade_date"], "close": rs[0]["close"], "change_pct": None,
                                     "deliv_pct": None, "deliv_avg_20": None, "deliv_ratio": None}

    async def backfill_history(self, db: Session, sessions: int = HISTORY_SESSIONS) -> dict:
        """
        Download older official end-of-day files (about a year), politely (one at a time), for the shares
        the app knows — so what delivery predicts can be measured rather than assumed. Resumable.
        """
        from models import NSEDaily, Stock

        if self._switched_off("bhavcopy"):
            return {"downloaded": 0, "off": True}
        known = {s for (s,) in db.query(Stock.symbol)}
        have = {d for (d,) in db.query(NSEDaily.trade_date).distinct()}
        today = datetime.now(IST).date()
        added, day = 0, today - timedelta(days=1)
        async with httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
            for _ in range(int(sessions * 7 / 5) + 30):
                if len(have) >= sessions:
                    break
                if day.weekday() < 5 and day not in have:
                    try:
                        response = await client.get(BHAV_URL.format(d=day))
                        if response.status_code == 200 and "SYMBOL" in response.text[:200]:
                            rows = parse_bhavcopy(response.text)
                            file_day = rows[0]["trade_date"] if rows else None
                            if file_day is not None and file_day not in have:
                                db.bulk_insert_mappings(NSEDaily, [r for r in rows if r["trade_date"] == file_day
                                                                   and r["symbol"] in known])
                                db.commit()
                                have.add(file_day)
                                added += 1
                    except IntegrityError:
                        db.rollback()               # another run stored this day meanwhile: skip it
                        have.add(day)
                    except Exception as exc:  # noqa: BLE001
                        db.rollback()
                        logger.warning("NSE history backfill stopped at %s: %s", day, exc)
                        break
                    await asyncio.sleep(0.5)        # be polite to NSE's servers
                day -= timedelta(days=1)
        if added:
            logger.info("NSE history: downloaded %d older session(s); %d on record", added, len(have))
        return {"downloaded": added, "sessions": len(have)}

    def load_delivery_edges(self, db: Session) -> None:
        """Read the measured delivery edges (source 'nse' pattern stats) into memory for the ranking."""
        from models import PatternStat

        rows = {r.pattern: r for r in db.query(PatternStat).filter(PatternStat.source == "nse")}
        base = rows.get("ALL")
        if not base or not base.occurrences:
            return
        up_rate = base.successes / base.occurrences * 100
        for pattern in ("delivery_buying", "delivery_selling"):
            r = rows.get(pattern)
            if r is None or r.occurrences < MIN_EDGE_SAMPLES:
                self.delivery_edges[pattern] = None
                continue
            rate = r.successes / r.occurrences * 100
            base_rate = up_rate if pattern == "delivery_buying" else 100 - up_rate
            self.delivery_edges[pattern] = {"n": r.occurrences, "rate": round(rate, 1),
                                            "base": round(base_rate, 1), "edge": round(rate - base_rate, 1)}

    # ------------------------------------------------------------------
    # Reads (sync, safe from worker threads)
    # ------------------------------------------------------------------
    def filings_for(self, symbol: str) -> list[dict]:
        return self._filings.get(symbol.upper(), [])

    def fill_missing_sessions(self, symbol: str, candles):
        """
        Add official NSE bars for recent sessions the price feed skipped. A missing day makes every
        indicator and "today's change" wrong; the feed was found missing a whole session for about a
        third of NIFTY 500 shares. Only sessions inside the feed's own date range are filled, and only
        when the official price sits on the same scale as the neighbouring bars (no corporate action
        in between), so a fill can never introduce a jump the series doesn't have.
        """
        import pandas as pd
        from data_provider import CORPORATE_ACTION_GAP

        sessions = self._sessions.get(symbol.upper())
        if not sessions or candles is None or candles.empty or "date" not in candles.columns:
            return candles
        dates = pd.to_datetime(candles["date"])
        tz = dates.dt.tz
        day_of = (dates.dt.tz_convert("Asia/Kolkata") if tz is not None else dates).dt.date
        have = set(day_of)
        first, last = min(have), max(have)
        low, high = CORPORATE_ACTION_GAP
        fills = []
        for s in sessions:
            d = s["trade_date"]
            if d in have or not (first < d < last) or None in (s.get("open"), s.get("high"), s.get("low")):
                continue
            before = candles.loc[day_of < d, "close"]
            if before.empty or not (low <= s["close"] / float(before.iloc[-1]) <= high):
                continue
            stamp = pd.Timestamp(datetime.combine(d, datetime.min.time())).tz_localize("Asia/Kolkata")                 + pd.Timedelta(hours=9, minutes=15)
            fills.append({"date": stamp if tz is not None else stamp.tz_localize(None), "open": s["open"],
                          "high": s["high"], "low": s["low"], "close": s["close"], "volume": int(s.get("volume") or 0)})
        if not fills:
            return candles
        out = pd.concat([candles, pd.DataFrame(fills)], ignore_index=True)
        out = out.sort_values("date", kind="stable").reset_index(drop=True)
        out["volume"] = out["volume"].astype("int64")
        out.attrs = dict(candles.attrs)
        out.attrs["nse_filled"] = [f["date"].date() for f in fills]
        return out

    def official_close(self, symbol: str) -> Optional[tuple[date, float]]:
        d = self._delivery.get(symbol.upper())
        if not d or (datetime.now(IST).date() - d["date"]).days > BHAV_MAX_AGE_DAYS:
            return None
        return d["date"], d["close"]

    def exchange_facts(self, symbol: str, today: Optional[date] = None) -> dict:
        """What NSE says about this share right now (empty values when unknown)."""
        symbol = symbol.upper()
        today = today or datetime.now(IST).date()
        results = next((e["date"] for e in sorted(self._calendar.get(symbol, []), key=lambda e: e["date"])
                        if e["results"] and today <= e["date"] <= today + timedelta(days=RESULTS_RISK_DAYS)), None)
        ban_date, ban = self._ban
        delivery = self._delivery.get(symbol)
        if delivery and (today - delivery["date"]).days > BHAV_MAX_AGE_DAYS:
            delivery = None
        return {
            "results_date": results,
            "fo_ban": symbol in ban if ban_date and (today - ban_date).days <= BHAV_MAX_AGE_DAYS else None,
            "delivery": delivery,
            "deals": self._deals.get(symbol, [])[:3],
            "filings": len(self._filings.get(symbol, [])),
        }

    def status(self) -> dict:
        return dict(self._status)


nse_service = NSEService()
