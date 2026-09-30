"""
Free market-data provider backed by Yahoo Finance's chart API.

Used whenever Zerodha Kite isn't connected (Kite's historical-data API also
needs a paid add-on), so the app can rank stocks, draw charts and grade
recommendations with no broker credentials at all. NSE symbols map to
"<SYMBOL>.NS"; index/global tickers ("^NSEI", "CL=F", ...) pass through as-is.

Prices are delayed by up to ~15 minutes, which is fine for the swing-trade
horizon the ranking engine targets.
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
import pandas as pd

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

IST = "Asia/Kolkata"
CANDLE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

# Kite interval name -> (Yahoo interval, max days of history Yahoo serves for it)
_INTERVALS: dict[str, tuple[str, int]] = {
    "minute": ("1m", 7),
    "5minute": ("5m", 59),
    "15minute": ("15m", 59),
    "30minute": ("30m", 59),
    "60minute": ("60m", 729),
    "day": ("1d", 3650),
    "week": ("1wk", 3650),
}

_CACHE_TTL_SECONDS = 60
_MAX_CONCURRENT_REQUESTS = 8
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def empty_candles() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDLE_COLUMNS)


# An overnight gap beyond these bounds (open vs previous close) is a corporate
# action (split, bonus, demerger), not trading: the NIFTY-50 names' largest real
# gaps since 2019 were about -17% (March 2020), while Yahoo left e.g. Trent's
# 2026 3:2 adjustment (-33%) and the Tata Motors demerger (-40%) unadjusted.
CORPORATE_ACTION_GAP = (0.75, 1.33)
_logged_adjustments: set[tuple[str, str]] = set()


def clean_daily_candles(frame: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    """
    Make daily candles fit for analysis:
    - drop placeholder bars (zero volume, open = high = low = close) that feeds
      insert for holidays and missing data; a fake flat bar distorts ATR, RSI
      and volume averages;
    - back-adjust prices (and volumes) before an unadjusted corporate action by
      the gap ratio, so indicators, 52-week levels and momentum see one
      continuous series.
    """
    if frame is None or frame.empty or not {"open", "high", "low", "close", "volume"} <= set(frame.columns):
        return frame
    df = frame.copy()
    volume = df["volume"].fillna(0)
    flat = (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])
    df = df[~((volume == 0) & flat)].reset_index(drop=True)
    if len(df) < 2:
        return df

    ratio = df["open"] / df["close"].shift(1)
    low, high = CORPORATE_ACTION_GAP
    for i in df.index[(ratio < low) | (ratio > high)]:
        factor = float(ratio[i])
        for col in ("open", "high", "low", "close"):
            df.loc[: i - 1, col] = df.loc[: i - 1, col] * factor
        df.loc[: i - 1, "volume"] = (df.loc[: i - 1, "volume"] / factor).round()
        when = str(df["date"].iloc[i])[:10] if "date" in df.columns else str(i)
        if (symbol, when) not in _logged_adjustments:
            _logged_adjustments.add((symbol, when))
            logger.info("Back-adjusted %s history for a corporate action on %s (factor %.4f)", symbol, when, factor)
    df["volume"] = df["volume"].astype("int64")
    return df


def to_yahoo_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.startswith("^") or "=" in symbol or "." in symbol:
        return symbol
    return f"{symbol}.NS"


class YahooProvider:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._cache: dict[tuple, tuple[float, pd.DataFrame, dict]] = {}

    def _get_client(self) -> httpx.AsyncClient:
        # Created lazily so the client binds to the running event loop
        # (the nightly script and the API server each have their own).
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True)
            self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chart(self, symbol: str, interval: str = "day", days: int = 90) -> tuple[pd.DataFrame, dict]:
        """
        OHLCV candles (ascending, tz-aware IST `date` column) plus Yahoo's
        `meta` block (regularMarketPrice, chartPreviousClose, 52-week range).
        Returns an empty frame and {} on any failure; never raises.
        """
        yahoo_interval, max_days = _INTERVALS.get(interval, ("1d", 3650))
        days = max(1, min(days, max_days))
        key = (symbol, yahoo_interval, days)

        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1], cached[2]

        client = self._get_client()
        now = datetime.now()
        params = {
            "period1": int((now - timedelta(days=days)).timestamp()),
            "period2": int(now.timestamp()) + 60,
            "interval": yahoo_interval,
            "includePrePost": "false",
        }
        url = f"{settings.YAHOO_CHART_URL}/{to_yahoo_symbol(symbol)}"

        try:
            async with self._semaphore:
                response = await client.get(url, params=params)
            if response.status_code != 200:
                logger.warning("Yahoo chart %s (%s) returned HTTP %s", symbol, yahoo_interval, response.status_code)
                return empty_candles(), {}
            result = (response.json().get("chart") or {}).get("result") or []
            if not result:
                return empty_candles(), {}
            frame, meta = self._parse(result[0])
            if yahoo_interval == "1d":
                frame = clean_daily_candles(frame, symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Yahoo chart fetch failed for %s: %s", symbol, exc)
            return empty_candles(), {}

        self._cache[key] = (time.monotonic(), frame, meta)
        return frame, meta

    @staticmethod
    def _parse(result: dict) -> tuple[pd.DataFrame, dict]:
        meta = result.get("meta") or {}
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not timestamps or not quote:
            return empty_candles(), meta

        frame = pd.DataFrame({
            "date": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(IST),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        })
        # Yahoo emits null rows for halted/illiquid intervals.
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        frame["volume"] = frame["volume"].fillna(0).astype("int64")
        # Occasional duplicate stamp for the live bar; keep the newest.
        frame = frame.drop_duplicates(subset="date", keep="last").reset_index(drop=True)
        return frame, meta

    async def get_candles(self, symbol: str, days: int = 90, interval: str = "day") -> pd.DataFrame:
        frame, _ = await self.chart(symbol, interval=interval, days=days)
        return frame

    async def get_quote(self, symbol: str) -> Optional[dict]:
        """Last price and day change for a stock, index or global ticker."""
        frame, meta = await self.chart(symbol, interval="day", days=10)
        if frame.empty:
            return None
        price = meta.get("regularMarketPrice") or float(frame["close"].iloc[-1])
        previous = float(frame["close"].iloc[-2]) if len(frame) >= 2 else meta.get("chartPreviousClose")
        change_pct = ((price / previous) - 1) * 100 if previous else None
        return {
            "symbol": symbol,
            "price": round(float(price), 2),
            "previous_close": round(float(previous), 2) if previous else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "as_of": frame["date"].iloc[-1].isoformat(),
        }


yahoo_provider = YahooProvider()
