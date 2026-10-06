"""
Option chains from NSE (indices such as NIFTY / BANKNIFTY, and every F&O stock).

Each chain is NSE's live snapshot for one expiry, stamped with NSE's own time, plus the reading traders
use, all computed from the data shown:
- PCR: total put open interest / total call open interest (above 1 = more puts written, often read as support);
- max pain: the expiry price at which option buyers, in total, would get the least;
- ATM IV: implied volatility at the strike nearest the underlying;
- support / resistance: the strikes with the most put / call open interest.
A response that looks broken (no underlying price, no strikes) is reported as unavailable, never shown as data.
"""
import time
from datetime import datetime
from typing import Optional

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/option-chain"}
EXPIRIES_URL = "https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}"
CHAIN_URL = "https://www.nseindia.com/api/option-chain-v3?type={type}&symbol={symbol}&expiry={expiry}"
CACHE_SECONDS = 60
STRIKES_EACH_SIDE = 15


def _num(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def max_pain(rows: list[dict]) -> Optional[float]:
    """Strike at which the total value of all options at expiry (paid to buyers) is smallest."""
    strikes = [r["strike"] for r in rows]
    if not strikes:
        return None
    def pain(price: float) -> float:
        return sum((r["ce_oi"] or 0) * max(0.0, price - r["strike"]) + (r["pe_oi"] or 0) * max(0.0, r["strike"] - price)
                   for r in rows)
    return min(strikes, key=pain)


def summarize(rows: list[dict], underlying: float) -> dict:
    ce_oi = sum(r["ce_oi"] or 0 for r in rows)
    pe_oi = sum(r["pe_oi"] or 0 for r in rows)
    atm = min(rows, key=lambda r: abs(r["strike"] - underlying)) if rows else None
    ivs = [v for v in ((atm or {}).get("ce_iv"), (atm or {}).get("pe_iv")) if v]
    support = max((r for r in rows if r["strike"] <= underlying), key=lambda r: r["pe_oi"] or 0, default=None)
    resistance = max((r for r in rows if r["strike"] >= underlying), key=lambda r: r["ce_oi"] or 0, default=None)
    return {
        "pcr": round(pe_oi / ce_oi, 2) if ce_oi else None,
        "max_pain": max_pain(rows),
        "atm_strike": atm["strike"] if atm else None,
        "atm_iv": round(sum(ivs) / len(ivs), 2) if ivs else None,
        "support": support["strike"] if support and support["pe_oi"] else None,
        "resistance": resistance["strike"] if resistance and resistance["ce_oi"] else None,
        "total_ce_oi": ce_oi, "total_pe_oi": pe_oi,
    }


def parse_chain(payload: dict, expiry: str) -> dict:
    records = (payload or {}).get("records") or {}
    underlying = _num(records.get("underlyingValue"))
    data = records.get("data") or (payload.get("filtered") or {}).get("data") or []
    rows = []
    for d in data:
        if d.get("expiryDates") not in (None, expiry):
            continue
        strike = _num(d.get("strikePrice"))
        if strike is None:
            continue
        ce, pe = d.get("CE") or {}, d.get("PE") or {}
        rows.append({"strike": strike,
                     "ce_ltp": _num(ce.get("lastPrice")), "ce_change": _num(ce.get("change")),
                     "ce_oi": _num(ce.get("openInterest")), "ce_oi_change": _num(ce.get("changeinOpenInterest")),
                     "ce_iv": _num(ce.get("impliedVolatility")) or None, "ce_volume": _num(ce.get("totalTradedVolume")),
                     "pe_ltp": _num(pe.get("lastPrice")), "pe_change": _num(pe.get("change")),
                     "pe_oi": _num(pe.get("openInterest")), "pe_oi_change": _num(pe.get("changeinOpenInterest")),
                     "pe_iv": _num(pe.get("impliedVolatility")) or None, "pe_volume": _num(pe.get("totalTradedVolume"))})
    rows.sort(key=lambda r: r["strike"])
    return {"underlying": underlying, "timestamp": records.get("timestamp"), "rows": rows}


class OptionsService:
    def __init__(self) -> None:
        self._cache: dict[tuple, tuple[float, dict]] = {}

    async def _get(self, client: httpx.AsyncClient, url: str) -> dict:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

    async def chain(self, symbol: str, expiry: Optional[str] = None) -> dict:
        from instruments_service import INDEX_BY_SYMBOL, instruments_service

        symbol = symbol.strip().upper()
        if not instruments_service.has_options(symbol):
            return {"available": False, "symbol": symbol, "error": f"{symbol} has no options on NSE."}
        key = (symbol, expiry)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        kind = "Indices" if symbol in INDEX_BY_SYMBOL else "Equity"
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=20, follow_redirects=True) as client:
                expiries = (await self._get(client, EXPIRIES_URL.format(symbol=symbol))).get("expiryDates") or []
                if not expiries:
                    raise ValueError("NSE returned no expiry dates")
                chosen = expiry if expiry in expiries else expiries[0]
                payload = await self._get(client, CHAIN_URL.format(type=kind, symbol=symbol, expiry=chosen))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Option chain for %s unavailable: %s", symbol, exc)
            return {"available": False, "symbol": symbol,
                    "error": "NSE's option chain couldn't be fetched right now — try again in a minute."}
        parsed = parse_chain(payload, chosen)
        if not parsed["underlying"] or not parsed["rows"]:
            return {"available": False, "symbol": symbol, "expiries": expiries, "expiry": chosen,
                    "error": "NSE returned an empty option chain for this expiry."}
        underlying, rows = parsed["underlying"], parsed["rows"]
        atm_i = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - underlying))
        window = rows[max(0, atm_i - STRIKES_EACH_SIDE): atm_i + STRIKES_EACH_SIDE + 1]
        result = {
            "available": True, "symbol": symbol, "expiry": chosen, "expiries": expiries[:12],
            "underlying": underlying, "as_of": parsed["timestamp"], "fetched_at": datetime.utcnow().isoformat() + "Z",
            "lot_size": (instruments_service.lookup(symbol) or {}).get("lot_size"),
            "summary": summarize(rows, underlying),        # over the whole chain, not just the strikes shown
            "rows": window, "strikes_total": len(rows),
        }
        self._cache[key] = (time.monotonic(), result)
        return result


options_service = OptionsService()
