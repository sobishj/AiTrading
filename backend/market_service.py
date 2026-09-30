"""
Market data and news aggregation:
- candles: Zerodha Kite when connected, otherwise Yahoo Finance (data_provider)
- news: several live Indian market RSS feeds, de-duplicated and time-stamped
- per-stock news matching on curated keywords plus word-boundary sentiment
- market overview: NIFTY trend and regime, India VIX, global cues, FII/DII flows
"""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import feedparser
import httpx
import pandas as pd

from config import settings
from data_provider import clean_daily_candles, empty_candles, yahoo_provider
from kite_service import kite_service
from utils.decorators import async_retry
from utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

_POSITIVE_WORDS = {
    "surge", "surges", "rally", "rallies", "gain", "gains", "upgrade", "upgrades", "beats", "beat",
    "record", "growth", "bullish", "outperform", "strong", "profit", "rises", "rise", "jump", "jumps",
    "soar", "soars", "buy", "positive", "boost", "boosts", "order", "orders", "wins", "win", "expansion",
    "approval", "approves", "dividend", "buyback", "upbeat", "climbs", "high",
}
_NEGATIVE_WORDS = {
    "fall", "falls", "drop", "drops", "plunge", "plunges", "loss", "losses", "downgrade", "downgrades",
    "miss", "misses", "weak", "bearish", "underperform", "decline", "declines", "crash", "sell", "negative",
    "cut", "cuts", "slump", "slumps", "concern", "concerns", "probe", "penalty", "fraud", "resigns",
    "tumble", "tumbles", "slides", "slide", "lower", "low", "sinks", "raid", "ban", "default",
}
EARNINGS_WORDS = {"results", "q1", "q2", "q3", "q4", "earnings", "profit", "revenue", "quarter", "ebitda", "margin"}
_WORD_RE = re.compile(r"[a-z0-9']+")

NEWS_CACHE_SECONDS = 180
NEWS_MAX_AGE_HOURS = 72
OVERVIEW_CACHE_SECONDS = 120
FII_DII_CACHE_SECONDS = 1800

# Yahoo tickers for the market overview panel.
INDEX_TICKERS: list[tuple[str, str]] = [
    ("^NSEI", "NIFTY 50"),
    ("^NSEBANK", "BANK NIFTY"),
    ("^INDIAVIX", "India VIX"),
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq"),
    ("^N225", "Nikkei 225"),
    ("^HSI", "Hang Seng"),
    ("CL=F", "Crude Oil"),
    ("INR=X", "USD/INR"),
]
BENCHMARK = "^NSEI"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_published(entry: dict) -> Optional[datetime]:
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    except (TypeError, ValueError):
        return None


_GROUP_COMPANY_WORDS = "Life|Card|Cards|General|Mutual|MF|Funds|Securities|AMC|Capital|Finance"


def keyword_pattern(keywords: list[str]) -> Optional[re.Pattern]:
    """
    One regex matching any keyword on word boundaries. Short all-caps
    keywords (BEL, HAL, ITC) stay case-sensitive so "hal" in ordinary prose
    doesn't count; longer ones are case-insensitive.
    """
    parts = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        escaped = re.escape(kw)
        if len(kw) <= 4 and kw.isupper():
            # "SBI" must not match "SBI Life"/"SBI Card" (different listed group companies).
            parts.append(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])(?!\s+(?:{_GROUP_COMPANY_WORDS}))")
        else:
            parts.append(rf"(?i:(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9]))")
    return re.compile("|".join(parts)) if parts else None


# Headlines that tend to move the whole market rather than one stock.
_MARKET_MOVING = re.compile(
    r"\b(rbi|repo rate|monetary policy|mpc|fed|fomc|interest rate|rate (cut|hike)|inflation|cpi|wpi|gdp|"
    r"budget|fiscal|crude|oil price|rupee|dollar index|bond yield|treasury|tariff|sanction|war|ceasefire|"
    r"sebi|election|fii|fpi|foreign investors|recession|stimulus|nifty|sensex|global markets|wall street)\b",
    re.IGNORECASE,
)


def is_market_moving(text: str) -> bool:
    """True for macro/policy/flow headlines that usually affect the broad market (a keyword rule, not a prediction)."""
    return bool(_MARKET_MOVING.search(text or ""))


def classify_regimes(trend: str, vix: Optional[float], nifty_change: Optional[float],
                     atr_pct: Optional[float], market_moving_news: int) -> list[str]:
    """
    Detailed market regimes (several can apply at once), each from measured data:
    BULLISH / BEARISH / SIDEWAYS from the NIFTY trend; HIGH_ / LOW_VOLATILITY from India VIX
    (or NIFTY ATR% when VIX is missing); NEWS_DRIVEN from recent market-moving headlines.
    """
    regimes = []
    regimes.append({"uptrend": "BULLISH", "downtrend": "BEARISH"}.get(trend, "SIDEWAYS"))
    if vix is not None:
        if vix >= 20:
            regimes.append("HIGH_VOLATILITY")
        elif vix <= 13:
            regimes.append("LOW_VOLATILITY")
    elif atr_pct is not None:
        if atr_pct >= 1.8:
            regimes.append("HIGH_VOLATILITY")
        elif atr_pct <= 0.8:
            regimes.append("LOW_VOLATILITY")
    if market_moving_news >= 4 or (market_moving_news >= 2 and nifty_change is not None and abs(nifty_change) >= 1.2):
        regimes.append("NEWS_DRIVEN")
    return regimes


def headline_sentiment(text: str) -> int:
    """+1 / 0 / -1 from whole-word keyword counts (no substring false hits like 'up' in 'update')."""
    words = set(_WORD_RE.findall(text.lower()))
    score = len(words & _POSITIVE_WORDS) - len(words & _NEGATIVE_WORDS)
    return (score > 0) - (score < 0)


class MarketService:
    def __init__(self) -> None:
        self._news_cache: list[dict] = []
        self._news_cache_at: float = 0.0
        self._instrument_token_cache: dict[str, int] = {}
        self._instrument_cache_time: Optional[datetime] = None
        self._overview_cache: Optional[dict] = None
        self._overview_cache_at: float = 0.0
        self._fii_dii_cache: Optional[dict] = None
        self._fii_dii_cache_at: float = 0.0

    # ------------------------------------------------------------------
    # Candle / price data
    # ------------------------------------------------------------------
    @property
    def data_source(self) -> str:
        mode = settings.MARKET_DATA_PROVIDER.lower()
        if mode == "kite" or (mode == "auto" and kite_service.is_configured):
            return "kite"
        return "yahoo"

    @async_retry(max_retries=2, base_delay=1.0)
    async def get_candles(self, instrument_token: int, days: int = 60,
                          interval: str = "day") -> pd.DataFrame:
        """Fetch OHLCV candles for a Kite instrument token."""
        if not kite_service.is_configured:
            return empty_candles()

        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)
        # The KiteConnect SDK is blocking; run it in a thread so concurrent
        # fetches (ranking_service.run_full_ranking) overlap.
        raw = await asyncio.to_thread(
            kite_service.get_historical_data, instrument_token, from_date, to_date, interval
        )
        if not raw:
            return empty_candles()
        frame = pd.DataFrame(raw)
        return clean_daily_candles(frame, str(instrument_token)) if interval == "day" else frame

    def _resolve_instrument_token(self, symbol: str) -> Optional[int]:
        """Resolve an NSE trading symbol to its Kite instrument_token (cached ~1h)."""
        if not kite_service.is_configured or not kite_service.kite:
            return None

        now = datetime.now()
        if (not self._instrument_cache_time or
                now - self._instrument_cache_time > timedelta(hours=1) or
                not self._instrument_token_cache):
            try:
                instruments = kite_service.kite.instruments("NSE")
                self._instrument_token_cache = {
                    i["tradingsymbol"]: i["instrument_token"] for i in instruments
                }
                self._instrument_cache_time = now
                logger.info("Cached %d NSE instrument tokens", len(self._instrument_token_cache))
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load instrument list: %s", exc)
                return None

        return self._instrument_token_cache.get(symbol.upper())

    async def get_candles_for_symbol(self, symbol: str, days: int = 60,
                                     interval: str = "day") -> pd.DataFrame:
        """Candles from Kite when connected, else Yahoo; Kite failures fall back to Yahoo."""
        if self.data_source == "kite":
            token = self._resolve_instrument_token(symbol)
            if token is not None:
                try:
                    candles = await self.get_candles(token, days=days, interval=interval)
                    if not candles.empty:
                        return candles
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Kite candles failed for %s, falling back to Yahoo: %s", symbol, exc)
        return await yahoo_provider.get_candles(symbol, days=days, interval=interval)

    async def validate_symbol_exists(self, symbol: str) -> Optional[dict]:
        """Confirm an NSE symbol has market data; returns Yahoo meta (incl. longName) or None."""
        frame, meta = await yahoo_provider.chart(symbol, interval="day", days=10)
        return meta if not frame.empty else None

    # ------------------------------------------------------------------
    # News / sentiment
    # ------------------------------------------------------------------
    async def fetch_news_async(self, force_refresh: bool = False) -> list[dict]:
        """Fetch all configured RSS feeds concurrently (3-minute cache), newest first."""
        if not force_refresh and self._news_cache and time.monotonic() - self._news_cache_at < NEWS_CACHE_SECONDS:
            return self._news_cache

        async with httpx.AsyncClient(headers=_BROWSER_HEADERS, timeout=15, follow_redirects=True) as client:
            responses = await asyncio.gather(
                *(client.get(url) for url in settings.news_feed_list), return_exceptions=True
            )

        cutoff = datetime.now(IST) - timedelta(hours=NEWS_MAX_AGE_HOURS)
        seen: set[str] = set()
        items: list[dict] = []
        for url, response in zip(settings.news_feed_list, responses):
            if isinstance(response, Exception) or response.status_code != 200:
                logger.warning("News feed failed: %s (%s)", url,
                               response if isinstance(response, Exception) else response.status_code)
                continue
            feed = feedparser.parse(response.content)
            source = feed.feed.get("title", url) if feed.feed else url
            for entry in feed.entries:
                title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
                published = _parse_published(entry)
                if not title or title.lower() in seen:
                    continue
                if published and published < cutoff:
                    continue  # stale (Moneycontrol's feeds, for example, stopped in 2024)
                seen.add(title.lower())
                summary = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))
                items.append({
                    "title": title,
                    "summary": re.sub(r"\s+", " ", summary).strip()[:400],
                    "published": published.isoformat() if published else "",
                    "link": entry.get("link", ""),
                    "source": source,
                    "sentiment": headline_sentiment(title + " " + summary),
                    "market_moving": is_market_moving(title),
                })

        items.sort(key=lambda i: i["published"], reverse=True)
        if items or not self._news_cache:
            self._news_cache = items
            self._news_cache_at = time.monotonic()
            logger.info("Fetched %d fresh news items from %d feeds", len(items), len(settings.news_feed_list))
        return self._news_cache

    def fetch_news(self) -> list[dict]:
        """Synchronous accessor for the cached news (populated by fetch_news_async)."""
        return self._news_cache

    def stock_sentiment(self, symbol: str, name: str, keywords: Optional[list[str]] = None) -> dict:
        """
        Sentiment 0-100 for a stock from cached headlines that mention it.
        50 = neutral or no news. Each matching headline votes +1/0/-1;
        the score moves 12 points per net vote, capped at 15-85, because
        headline counts are a weak signal on their own.
        """
        terms = list(keywords or [])
        terms += [symbol] if len(symbol) > 3 else []
        if not terms:
            terms = [name]
        pattern = keyword_pattern(terms)
        news = self._news_cache
        relevant = [item for item in news if pattern and pattern.search(item["title"] + " " + item["summary"])]
        if not relevant:
            return {"sentiment": "neutral", "score": 50.0, "matched_headlines": [], "news_count": 0,
                    "earnings_news": False, "headlines": []}

        net = sum(item["sentiment"] for item in relevant[:8])
        score = max(15.0, min(85.0, 50.0 + 12.0 * net))
        label = "positive" if score > 55 else "negative" if score < 45 else "neutral"
        earnings = any(set(_WORD_RE.findall(item["title"].lower())) & EARNINGS_WORDS for item in relevant[:5])
        return {
            "sentiment": label,
            "score": score,
            "matched_headlines": [item["title"] for item in relevant[:5]],
            "headlines": [
                {k: item[k] for k in ("title", "link", "published", "source", "sentiment")}
                for item in relevant[:5]
            ],
            "news_count": len(relevant),
            "earnings_news": earnings,
        }

    # ------------------------------------------------------------------
    # FII/DII flows (NSE public API; needs a cookie from the home page)
    # ------------------------------------------------------------------
    async def get_fii_dii_activity_async(self) -> dict:
        if self._fii_dii_cache and time.monotonic() - self._fii_dii_cache_at < FII_DII_CACHE_SECONDS:
            return self._fii_dii_cache
        result = {"fii_activity": None, "dii_activity": None, "date": None, "source": "unavailable"}
        try:
            async with httpx.AsyncClient(headers=_BROWSER_HEADERS, timeout=12, follow_redirects=True) as client:
                await client.get("https://www.nseindia.com/")
                response = await client.get(
                    "https://www.nseindia.com/api/fiidiiTradeReact",
                    headers={"Referer": "https://www.nseindia.com/reports/fii-dii"},
                )
            rows = response.json() if response.status_code == 200 else []
            for row in rows:
                category = str(row.get("category", "")).upper()
                net = float(str(row.get("netValue", "0")).replace(",", ""))
                if category.startswith("FII"):
                    result["fii_activity"] = net
                elif category.startswith("DII"):
                    result["dii_activity"] = net
                result["date"] = row.get("date")
            if result["fii_activity"] is not None or result["dii_activity"] is not None:
                result["source"] = "NSE"
        except Exception as exc:  # noqa: BLE001
            logger.warning("FII/DII fetch failed: %s", exc)
        if result["source"] == "NSE" or self._fii_dii_cache is None:
            self._fii_dii_cache, self._fii_dii_cache_at = result, time.monotonic()
        return self._fii_dii_cache

    def get_fii_dii_activity(self) -> dict:
        """Last fetched FII/DII values (sync; refreshed by get_fii_dii_activity_async)."""
        return self._fii_dii_cache or {"fii_activity": None, "dii_activity": None, "date": None,
                                       "source": "unavailable"}

    # ------------------------------------------------------------------
    # Market overview: indices, regime, global cues
    # ------------------------------------------------------------------
    async def get_market_overview(self, force_refresh: bool = False) -> dict:
        if (not force_refresh and self._overview_cache
                and time.monotonic() - self._overview_cache_at < OVERVIEW_CACHE_SECONDS):
            return self._overview_cache

        from analysis_service import analysis_service  # local import: analysis has no deps on us

        quotes, nifty_candles, fii_dii = await asyncio.gather(
            asyncio.gather(*(yahoo_provider.get_quote(t) for t, _ in INDEX_TICKERS)),
            yahoo_provider.get_candles(BENCHMARK, days=settings.HISTORY_DAYS),
            self.get_fii_dii_activity_async(),
        )
        indices = []
        for (ticker, label), quote in zip(INDEX_TICKERS, quotes):
            if quote:
                indices.append({"ticker": ticker, "name": label, "price": quote["price"],
                                "change_pct": quote["change_pct"]})
        by_name = {i["name"]: i for i in indices}

        nifty = analysis_service.compute_indicators("NIFTY 50", nifty_candles)
        vix = by_name.get("India VIX", {}).get("price")
        nifty_change = by_name.get("NIFTY 50", {}).get("change_pct")

        if nifty.has_data and nifty.trend == "uptrend" and (vix is None or vix < 16):
            regime = "risk-on"
        elif (not nifty.has_data or nifty.trend == "downtrend" or (vix is not None and vix > 20)
              or (nifty_change is not None and nifty_change <= -1.0)):
            regime = "risk-off"
        else:
            regime = "neutral"

        recent_cutoff = (datetime.now(IST) - timedelta(hours=12)).isoformat()
        moving = sum(1 for n in self._news_cache if n.get("market_moving") and n.get("published", "") >= recent_cutoff)
        overview = {
            "as_of": datetime.now(IST).isoformat(),
            "regime": regime,
            "regimes": classify_regimes(nifty.trend if nifty.has_data else "unknown", vix, nifty_change,
                                        nifty.atr_pct, moving),
            "market_moving_news": moving,
            "nifty": {
                "price": nifty.close, "change_pct": nifty_change, "trend": nifty.trend,
                "ema_20": nifty.ema_20, "ema_50": nifty.ema_50, "ema_200": nifty.ema_200,
                "rsi": nifty.rsi, "return_20d": nifty.return_20d,
            },
            "vix": vix,
            "indices": indices,
            "fii_dii": fii_dii,
            "summary": self._overview_summary(nifty, nifty_change, vix, regime, by_name, fii_dii),
        }
        self._overview_cache, self._overview_cache_at = overview, time.monotonic()
        return overview

    @staticmethod
    def _overview_summary(nifty, nifty_change, vix, regime, by_name: dict, fii_dii: dict) -> str:
        if not nifty.has_data:
            return "Market data unavailable right now."
        parts = [
            f"NIFTY 50 at {nifty.close:,.0f}"
            + (f" ({nifty_change:+.2f}% today)" if nifty_change is not None else "")
            + f", {nifty.trend}"
            + (f", {nifty.return_20d:+.1f}% over 20 sessions" if nifty.return_20d is not None else "")
            + "."
        ]
        if vix is not None:
            mood = "calm" if vix < 14 else "normal" if vix < 18 else "elevated" if vix < 22 else "fearful"
            parts.append(f"India VIX {vix:.1f} ({mood}).")
        cues = [f"{n} {by_name[n]['change_pct']:+.1f}%" for n in ("S&P 500", "Nasdaq", "Nikkei 225", "Crude Oil")
                if n in by_name and by_name[n]["change_pct"] is not None]
        if cues:
            parts.append("Global: " + ", ".join(cues) + ".")
        if fii_dii.get("fii_activity") is not None:
            parts.append(f"FII net ₹{fii_dii['fii_activity']:,.0f} cr, DII net ₹{fii_dii['dii_activity']:,.0f} cr"
                         f" ({fii_dii.get('date')}).")
        # Only state what the data and the engine's rules support (see scripts/evaluate_ranking.py).
        parts.append({
            "risk-on": "Regime: risk-on (NIFTY in an uptrend, VIX calm).",
            "neutral": "Regime: neutral.",
            "risk-off": "Regime: risk-off — fresh longs carry extra risk; size down.",
        }[regime])
        if nifty.ema_50 is not None and nifty.close < nifty.ema_50:
            parts.append(f"NIFTY is below its 50-day EMA ({nifty.ema_50:,.0f}), so the engine makes no new BUY calls "
                         "until it recovers.")
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Shared market-context summary (GET /api/market-context, morning brief)
    # ------------------------------------------------------------------
    async def summarize_market_context(self, force_refresh_news: bool = False, use_llm: bool = False) -> dict:
        overview = await self.get_market_overview(force_refresh=force_refresh_news)
        news = await self.fetch_news_async(force_refresh=force_refresh_news)
        headlines = [h["title"] for h in news[:10]]
        summary = overview["summary"]

        if use_llm:
            from llm_service import llm_service  # local import: avoids a module-load-order cycle
            summary = await llm_service.interpret_market_context({
                "fii_activity": overview["fii_dii"].get("fii_activity"),
                "dii_activity": overview["fii_dii"].get("dii_activity"),
                "headlines": "\n".join(f"- {h}" for h in headlines) or "No headlines available.",
                "global_indicators": overview["summary"],
            }, fallback=overview["summary"])

        return {
            "fii_activity": overview["fii_dii"].get("fii_activity"),
            "dii_activity": overview["fii_dii"].get("dii_activity"),
            "news_sentiment": summary,
            "global_market_summary": overview["summary"],
            "headlines": headlines,
            "overview": overview,
        }

    # ------------------------------------------------------------------
    # Option chain (requires Kite; degrades gracefully without it)
    # ------------------------------------------------------------------
    def get_option_chain(self, symbol: str) -> list[dict]:
        if not kite_service.is_configured or not kite_service.kite:
            return []
        try:
            instruments = kite_service.kite.instruments("NFO")
            return [i for i in instruments if i.get("name") == symbol.upper()]
        except Exception as exc:  # noqa: BLE001
            logger.error("Option chain fetch failed for %s: %s", symbol, exc)
            return []


market_service = MarketService()
