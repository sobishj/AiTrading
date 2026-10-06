"""
Everything you can look up and add: every share listed on NSE, every ETF, the main NIFTY indices, and which
of them have futures & options.

- Shares and ETFs come from NSE's own lists (refreshed weekly, cached on disk), so a search by name works:
  "HDFC" finds HDFC Bank, HDFC Life and HDFC AMC; a name that isn't listed (e.g. "NSE" itself, or the old HDFC
  Ltd that merged into HDFC Bank in 2023) is reported as not listed instead of failing silently.
- Indices: daily history comes from Yahoo when it has a full record, otherwise from NSE's official all-indices
  file (open/high/low/close for every index, every session) — an index is only offered with real history.
- F&O: NSE's market-lot file says which underlyings have options and their lot sizes.
"""
import asyncio
import csv
import io
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
CACHE = Path(__file__).resolve().parent / "cache" / "instruments.json"
REFRESH_SECONDS = 7 * 24 * 3600
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
ETF_URL = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"
FO_LOTS_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
INDEX_CLOSE_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv"
INDEX_HISTORY_SESSIONS = 260
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36"}


@dataclass(frozen=True)
class Index:
    symbol: str                 # what the app stores and shows (also NSE's option symbol where options exist)
    name: str
    nse_name: str               # the name in NSE's all-indices file
    yahoo: Optional[str]        # Yahoo symbol when Yahoo has a full daily history (also gives intraday bars)
    options: bool = False
    aliases: tuple = ()


INDICES = (
    Index("NIFTY", "NIFTY 50", "Nifty 50", "^NSEI", True, ("nifty 50", "nifty50", "nifty")),
    Index("BANKNIFTY", "NIFTY Bank", "Nifty Bank", "^NSEBANK", True, ("bank nifty", "nifty bank")),
    Index("FINNIFTY", "NIFTY Financial Services", "Nifty Financial Services", None, True,
          ("fin nifty", "nifty fin service", "financial services")),
    Index("MIDCPNIFTY", "NIFTY Midcap Select", "Nifty Midcap Select", None, True, ("midcap select", "midcp nifty")),
    Index("NIFTYNXT50", "NIFTY Next 50", "Nifty Next 50", "^NSMIDCP", True, ("nifty next 50", "next 50", "junior")),
    Index("NIFTYIT", "NIFTY IT", "Nifty IT", "^CNXIT", False, ("nifty it", "it index")),
    Index("NIFTYPHARMA", "NIFTY Pharma", "Nifty Pharma", "^CNXPHARMA", False, ("nifty pharma",)),
    Index("NIFTYAUTO", "NIFTY Auto", "Nifty Auto", None, False, ("nifty auto",)),
    Index("NIFTYFMCG", "NIFTY FMCG", "Nifty FMCG", None, False, ("nifty fmcg",)),
    Index("NIFTYMETAL", "NIFTY Metal", "Nifty Metal", None, False, ("nifty metal",)),
    Index("NIFTYREALTY", "NIFTY Realty", "Nifty Realty", None, False, ("nifty realty",)),
    Index("NIFTYENERGY", "NIFTY Energy", "Nifty Energy", None, False, ("nifty energy",)),
    Index("NIFTYPSUBANK", "NIFTY PSU Bank", "Nifty PSU Bank", None, False, ("psu bank",)),
    Index("NIFTYMIDCAP100", "NIFTY Midcap 100", "Nifty Midcap 100", None, False, ("midcap 100", "nifty midcap")),
    Index("NIFTYSMLCAP100", "NIFTY Smallcap 100", "Nifty Smallcap 100", None, False, ("smallcap 100", "nifty smallcap")),
    Index("NIFTY500", "NIFTY 500", "Nifty 500", "^CRSLDX", False, ("nifty 500",)),
    Index("INDIAVIX", "India VIX", "India VIX", "^INDIAVIX", False, ("vix", "india vix", "volatility")),
)
INDEX_BY_SYMBOL = {i.symbol: i for i in INDICES}
INDEX_BY_NSE_NAME = {i.nse_name.lower(): i for i in INDICES}
# Names people search for that aren't listed shares, with what to say instead.
NOT_LISTED = {
    "nse": "NSE (the National Stock Exchange) isn't a listed company, so its shares don't trade on NSE. Looking for "
           "the market as a whole? Try NIFTY 50.",
    "hdfc": "The old HDFC Ltd merged into HDFC Bank in July 2023 and no longer trades — HDFC Bank, HDFC Life and "
            "HDFC AMC are listed below.",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9& ]+", " ", (text or "").lower()).strip()


def _clean_company(name: str) -> str:
    return re.sub(r"[\s,]+(ltd\.?|limited)$", "", (name or "").strip(), flags=re.IGNORECASE).strip()


def parse_equity_list(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿")), skipinitialspace=True):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        symbol, series = r.get("SYMBOL", "").upper(), r.get("SERIES", "")
        if symbol and series in ("EQ", "BE", "BZ", "SM", "ST"):
            rows.append({"symbol": symbol, "name": _clean_company(r.get("NAME OF COMPANY", symbol)), "type": "equity",
                         "series": series})
    return rows


def parse_etf_list(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿")), skipinitialspace=True):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        symbol = r.get("Symbol", "").upper()
        if symbol:
            rows.append({"symbol": symbol, "name": f"{symbol} — ETF on {r.get('Underlying Asset') or 'an index'}",
                         "type": "etf", "series": "EQ"})
    return rows


def parse_fo_lots(text: str) -> dict[str, int]:
    """Underlying symbol -> current lot size (first month column)."""
    out = {}
    for line in text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1] and parts[1].upper() != "SYMBOL":
            try:
                out[parts[1].upper()] = int(float(parts[2]))
            except ValueError:
                continue
    return out


def parse_index_closes(text: str) -> list[dict]:
    """NSE's all-indices daily file -> rows for the indices the app knows."""
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿")), skipinitialspace=True):
        index = INDEX_BY_NSE_NAME.get((r.get("Index Name") or "").strip().lower())
        if index is None:
            continue
        try:
            when = datetime.strptime(r["Index Date"].strip(), "%d-%m-%Y").date()
            num = lambda k: float(r[k]) if r.get(k) not in (None, "", "-") else None  # noqa: E731
            close = num("Closing Index Value")
        except (KeyError, ValueError):
            continue
        if not close:
            continue
        rows.append({"trade_date": when, "symbol": index.symbol, "series": "IDX",
                     "open": num("Open Index Value") or close, "high": num("High Index Value") or close,
                     "low": num("Low Index Value") or close, "close": close,
                     "prev_close": None, "volume": int(num("Volume") or 0) or None,
                     "deliv_qty": None, "deliv_pct": None})
    return rows


class InstrumentsService:
    def __init__(self) -> None:
        self._listed: list[dict] = []
        self._by_symbol: dict[str, dict] = {}
        self._fo_lots: dict[str, int] = {}
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------
    def _set(self, listed: list[dict], fo_lots: dict[str, int]) -> None:
        self._listed = listed
        self._by_symbol = {r["symbol"]: r for r in listed}
        self._fo_lots = fo_lots

    def _load_cache(self) -> bool:
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            self._set(data["listed"], data["fo_lots"])
            self._loaded_at = data.get("saved", 0)
            return bool(self._listed)
        except (OSError, ValueError, KeyError):
            return False

    async def ensure_loaded(self, force: bool = False) -> None:
        if not self._listed:
            self._load_cache()
        if not force and self._listed and time.time() - self._loaded_at < REFRESH_SECONDS:
            return
        async with self._lock:
            if not force and self._listed and time.time() - self._loaded_at < REFRESH_SECONDS:
                return
            try:
                async with httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
                    eq, etf, lots = await asyncio.gather(client.get(EQUITY_URL), client.get(ETF_URL),
                                                         client.get(FO_LOTS_URL))
                listed = parse_equity_list(eq.text) if eq.status_code == 200 else []
                if len(listed) < 1000:
                    raise ValueError(f"NSE equity list looked incomplete ({len(listed)} rows)")
                listed += parse_etf_list(etf.text) if etf.status_code == 200 else []
                fo_lots = parse_fo_lots(lots.text) if lots.status_code == 200 else self._fo_lots
                self._set(listed, fo_lots)
                self._loaded_at = time.time()
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps({"saved": self._loaded_at, "listed": listed, "fo_lots": fo_lots}),
                                 encoding="utf-8")
                logger.info("Instrument lists: %d shares/ETFs, %d F&O underlyings", len(listed), len(fo_lots))
            except Exception as exc:  # noqa: BLE001  (keep the cached/previous lists)
                logger.warning("NSE instrument lists not refreshed: %s", exc)

    # ------------------------------------------------------------------
    # Lookup and search
    # ------------------------------------------------------------------
    def lookup(self, symbol: str) -> Optional[dict]:
        """{symbol, name, type: equity|etf|index, has_options, lot_size} or None if NSE doesn't list it."""
        symbol = (symbol or "").strip().upper()
        index = INDEX_BY_SYMBOL.get(symbol)
        if index:
            return {"symbol": index.symbol, "name": index.name, "type": "index", "has_options": index.options,
                    "lot_size": self._fo_lots.get(index.symbol)}
        row = self._by_symbol.get(symbol)
        if row is None:
            return None
        return {**row, "has_options": symbol in self._fo_lots, "lot_size": self._fo_lots.get(symbol)}

    def has_options(self, symbol: str) -> bool:
        symbol = (symbol or "").upper()
        index = INDEX_BY_SYMBOL.get(symbol)
        return index.options if index else symbol in self._fo_lots

    def search(self, query: str, limit: int = 10) -> dict:
        q = _norm(query)
        if not q:
            return {"results": [], "note": None}
        qs = q.replace(" ", "")
        scored = []
        for index in INDICES:
            names = [_norm(index.name), *index.aliases, index.symbol.lower()]
            if qs == index.symbol.lower() or q in index.aliases:
                scored.append((0, index.name, self.lookup(index.symbol)))
            elif any(n.startswith(q) or q in n for n in names):
                scored.append((2, index.name, self.lookup(index.symbol)))
        for row in self._listed:
            sym, name = row["symbol"].lower(), _norm(row["name"])
            words = name.split()
            if sym == qs:
                rank = 1
            elif sym.startswith(qs):
                rank = 3
            elif name.startswith(q) or any(w.startswith(q) for w in words):
                rank = 4
            elif q in name and len(q) >= 4:      # inside a word: only for longer searches ("nse" ≠ "finserv")
                rank = 6
            else:
                continue
            if row["type"] == "etf":
                rank += 1                     # shares first; ETFs right after
            scored.append((rank, row["name"], self.lookup(row["symbol"])))
        scored.sort(key=lambda x: (x[0], len(x[1]), x[1]))
        results = [r for _, _, r in scored[:limit]]
        note = NOT_LISTED.get(q)
        if not results and note is None:
            note = (f"No share, ETF or index on NSE matches \"{query.strip()}\". Check the spelling, or search by the "
                    "company's name.")
        return {"results": results, "note": note}

    # ------------------------------------------------------------------
    # Index history (NSE's official all-indices file)
    # ------------------------------------------------------------------
    async def backfill_index_history(self, db, sessions: int = INDEX_HISTORY_SESSIONS) -> dict:
        from models import NSEDaily

        have = {d for (d,) in db.query(NSEDaily.trade_date).filter(NSEDaily.series == "IDX").distinct()}
        today = datetime.now(IST).date()
        day, added = today, 0
        async with httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
            for _ in range(int(sessions * 7 / 5) + 30):
                if len(have) >= sessions:
                    break
                if day.weekday() < 5 and day not in have and not (day == today and datetime.now(IST).hour < 19):
                    try:
                        response = await client.get(INDEX_CLOSE_URL.format(d=day))
                        if response.status_code == 200 and "Index Name" in response.text[:200]:
                            rows = parse_index_closes(response.text)
                            file_day = rows[0]["trade_date"] if rows else None
                            if file_day is not None and file_day not in have:
                                db.bulk_insert_mappings(NSEDaily, [r for r in rows if r["trade_date"] == file_day])
                                db.commit()
                                have.add(file_day)
                                added += 1
                    except Exception as exc:  # noqa: BLE001
                        db.rollback()
                        if "unique" not in str(exc).lower():
                            logger.warning("Index history backfill stopped at %s: %s", day, exc)
                            break
                        have.add(day)
                    await asyncio.sleep(0.4)
                day -= timedelta(days=1)
        if added:
            logger.info("Index history: downloaded %d session(s); %d on record", added, len(have))
        return {"downloaded": added, "sessions": len(have)}

    @staticmethod
    def index_candles(db, symbol: str, days: int):
        """Daily candles for an index from NSE's official file (oldest first), as a DataFrame."""
        import pandas as pd
        from models import NSEDaily

        since = datetime.now(IST).date() - timedelta(days=days)
        rows = (db.query(NSEDaily).filter(NSEDaily.symbol == symbol, NSEDaily.series == "IDX",
                                          NSEDaily.trade_date >= since).order_by(NSEDaily.trade_date).all())
        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        stamps = [pd.Timestamp(datetime.combine(r.trade_date, datetime.min.time())).tz_localize("Asia/Kolkata")
                  + pd.Timedelta(hours=9, minutes=15) for r in rows]
        return pd.DataFrame({"date": stamps, "open": [float(r.open or r.close) for r in rows],
                             "high": [float(r.high or r.close) for r in rows], "low": [float(r.low or r.close) for r in rows],
                             "close": [float(r.close) for r in rows], "volume": [int(r.volume or 0) for r in rows]})


instruments_service = InstrumentsService()
