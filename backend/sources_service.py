"""
Data sources the app reads for research (Settings -> Data sources).

- News feeds (RSS/Atom): the four the app ships with, plus any you add. A feed is test-fetched
  before it is saved, so a wrong URL is caught at once rather than silently returning nothing.
- NSE's official data (filings, results calendar, end-of-day prices with delivery, F&O ban list,
  bulk/block deals): built in; can be switched off, never deleted.

Arbitrary web pages are deliberately not a source type: they have no stable structure, often forbid
scraping, and text hidden in a page could steer an AI that reads it. A site worth reading without a
feed gets its own connector instead (as NSE did).
"""
import ipaddress
import socket
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import feedparser
import httpx
from sqlalchemy.orm import Session

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

NSE_SOURCES = (
    ("nse_filings", "NSE company filings",
     "Results, orders, management changes, regulatory action — tied to each share by symbol."),
    ("nse_calendar", "NSE results calendar", "Upcoming board meetings for results: flags gap risk within a week."),
    ("nse_bhavcopy", "NSE official closing prices & delivery",
     "Cross-checks the price feed, fills sessions it skipped, and gives delivery %."),
    ("nse_ban", "NSE F&O ban list", "Shares whose open interest is above 95% of the limit."),
    ("nse_deals", "NSE bulk & block deals", "Large investors' trades, given to the AI as facts."),
)
KINDS = {"rss"} | {k for k, _, _ in NSE_SOURCES}
MAX_FEED_BYTES = 3_000_000
CACHE_SECONDS = 30
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36"}


FRIENDLY_NAMES = {
    "1977021501.cms": "Economic Times — Markets", "2146842.cms": "Economic Times — Stocks",
    "livemint.com/rss/markets": "LiveMint — Markets", "markets-106.rss": "Business Standard — Markets",
}


def _feed_name(url: str) -> str:
    for key, name in FRIENDLY_NAMES.items():
        if key in url:
            return name
    host = urlparse(url).netloc.removeprefix("www.")
    return host or url


def check_url(url: str) -> Optional[str]:
    """None if the URL may be fetched, else why not (http/https to a public host only)."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return "not a valid URL"
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "use a full http:// or https:// address"
    if parsed.username or parsed.password:
        return "addresses with a user name or password aren't accepted"
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return f"can't find the site {parsed.hostname}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return "addresses on this computer or your local network aren't accepted as research sources"
    return None


async def test_feed(url: str) -> dict:
    """Fetch and parse a feed; returns {ok, title, items:[{title, published}], error}."""
    problem = check_url(url)
    if problem:
        return {"ok": False, "error": problem}
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
            response = await client.get(url.strip())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"couldn't reach the site ({exc.__class__.__name__})"}
    if response.status_code != 200:
        return {"ok": False, "error": f"the site answered HTTP {response.status_code}"}
    if len(response.content) > MAX_FEED_BYTES:
        return {"ok": False, "error": "the page is too large to be a news feed"}
    feed = feedparser.parse(response.content)
    if not feed.entries:
        return {"ok": False, "error": "no news items found — this doesn't look like an RSS/Atom feed. Look for an "
                                      "'RSS' link on the site and use that address."}
    items = [{"title": " ".join((e.get("title") or "").split()), "published": e.get("published") or e.get("updated")}
             for e in feed.entries[:5]]
    return {"ok": True, "title": (feed.feed.get("title") or _feed_name(url)).strip()[:120], "items": items,
            "count": len(feed.entries)}


class SourcesService:
    def __init__(self) -> None:
        self._cache: Optional[list] = None
        self._cache_at = 0.0

    def invalidate(self) -> None:
        self._cache = None

    def seed(self, db: Session) -> None:
        """Create the built-in sources once (news feeds from config, NSE sources)."""
        from models import DataSource

        kinds = {k for (k,) in db.query(DataSource.kind).distinct()}
        if "rss" not in kinds:
            for url in settings.news_feed_list:
                db.add(DataSource(kind="rss", name=_feed_name(url), url=url, enabled=True, builtin=True))
        for kind, name, notes in NSE_SOURCES:
            if kind not in kinds:
                db.add(DataSource(kind=kind, name=name, enabled=True, builtin=True, notes=notes))
        db.commit()
        self.invalidate()

    def _rows(self) -> list:
        if self._cache is None or time.monotonic() - self._cache_at > CACHE_SECONDS:
            try:
                from database import db_session
                from models import DataSource
                with db_session() as db:
                    self._cache = [(r.id, r.kind, r.name, r.url, r.enabled) for r in db.query(DataSource).all()]
                self._cache_at = time.monotonic()
            except Exception as exc:  # noqa: BLE001  (DB unavailable: fall back to config defaults)
                logger.warning("Data sources unavailable, using defaults: %s", exc)
                return []
        return self._cache

    def enabled_feeds(self) -> list[tuple[str, str]]:
        """(url, name) of the news feeds switched on; the config list if nothing is configured yet."""
        rows = self._rows()
        if not any(kind == "rss" for _, kind, *_ in rows):
            return [(u, _feed_name(u)) for u in settings.news_feed_list]
        return [(url, name) for _, kind, name, url, enabled in rows if kind == "rss" and enabled and url]

    def is_enabled(self, kind: str) -> bool:
        rows = [r for r in self._rows() if r[1] == kind]
        return all(r[4] for r in rows) if rows else True

    def record(self, kind: str, ok: bool, items: Optional[int] = None, error: Optional[str] = None,
               url: Optional[str] = None) -> None:
        """Remember the last fetch result of a source (shown in Settings -> Data sources)."""
        try:
            from database import db_session
            from models import DataSource
            with db_session() as db:
                q = db.query(DataSource).filter(DataSource.kind == kind)
                if url:
                    q = q.filter(DataSource.url == url)
                for row in q.all():
                    if ok:
                        row.last_ok_at, row.last_error, row.last_items = datetime.utcnow(), None, items
                    else:
                        row.last_error = (error or "failed")[:300]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Source status not recorded: %s", exc)


sources_service = SourcesService()
