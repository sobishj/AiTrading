"""
Recognises trades you report in chat ("I bought 10 of this at 265 and sold at 280 yesterday")
so they can be recorded — with deterministic parsing, not an LLM, and never saved without
your confirmation.

Only past-tense statements count (bought / purchased / sold / booked …); questions such as
"should I buy at 250?" are ignored. Anything missing (usually the quantity) is left empty
for you to fill in on the confirmation card — it is never guessed.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

_BUY = r"(?:bought|purchased|picked\s+up|added|averaged(?:\s+down|\s+up)?|entered|got\s+in)"
_SELL = r"(?:sold|booked(?:\s+profits?|\s+loss(?:es)?)?|exited|closed|squared\s+off|got\s+out)"
_QUESTION = re.compile(r"\?|^\s*(should|shall|can|could|would|what|when|where|why|how|is|are|do|does)\b", re.I)
_PRICE = re.compile(r"(?:@|\bat\b|\bfor\b|\bprice\b|₹|\brs\.?|\binr\b)\s*(?:₹|rs\.?\s*)?(\d[\d,]*(?:\.\d+)?)", re.I)
_QTY = re.compile(r"(\d[\d,]*)\s*(?:shares?|qty|quantity|units?|nos?|stocks?)\b|\b(?:qty|quantity)\s*[:=]?\s*(\d[\d,]*)",
                  re.I)
_QTY_AFTER_VERB = re.compile(rf"(?:{_BUY}|{_SELL})\s+(\d[\d,]*)\b(?!\s*(?:%|rs|₹|/|-|\.\d))", re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass
class TradeAction:
    side: str                       # BUY | SELL
    price: float
    quantity: Optional[float] = None
    trade_date: Optional[date] = None


@dataclass
class TradeIntent:
    actions: list[TradeAction] = field(default_factory=list)
    symbol: Optional[str] = None


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_date(text: str, today: date) -> Optional[date]:
    t = text.lower()
    if re.search(r"\byesterday\b", t):
        return today - timedelta(days=1)
    if re.search(r"\btoday\b", t):
        return today
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s*,?\s*(\d{4}))?", t)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        d = _safe_date(year, _MONTHS[m.group(2)], int(m.group(1)))
        if d and not m.group(3) and d > today:
            d = _safe_date(year - 1, d.month, d.day)
        return d
    m = re.search(r"\bon\s+(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", t)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        year = year + 2000 if year < 100 else year
        return _safe_date(year, int(m.group(2)), int(m.group(1)))     # Indian order: day/month
    return None


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse(text: str, today: Optional[date] = None) -> Optional[TradeIntent]:
    """Return the trades a chat message reports, or None if it isn't a trade report."""
    today = today or date.today()
    if not text or _QUESTION.search(text.strip()):
        return None
    if not re.search(rf"\b{_BUY}\b|\b{_SELL}\b", text, re.I):
        return None
    # Split into clauses at each trade verb so "bought at 12 and sold at 50" gives two actions.
    spans = [(m.start(), "BUY" if re.match(_BUY, m.group(0), re.I) else "SELL")
             for m in re.finditer(rf"\b(?:{_BUY}|{_SELL})\b", text, re.I)]
    intent = TradeIntent()
    shared_date = parse_date(text, today)
    shared_qty = None
    for idx, (start, side) in enumerate(spans):
        end = spans[idx + 1][0] if idx + 1 < len(spans) else len(text)
        clause = text[start:end]
        price_m = _PRICE.search(clause)
        if not price_m:
            continue
        # Whatever precedes the price in this clause may hold the quantity
        before_price = clause[: price_m.start()]
        qty_m = _QTY.search(before_price) or _QTY.search(clause[price_m.end():]) or _QTY_AFTER_VERB.search(before_price)
        qty = None
        if qty_m:
            qty = _num(next(g for g in qty_m.groups() if g))
        if qty is None and side == "SELL" and shared_qty is not None:
            qty = shared_qty                     # "bought 10 at 12 and sold at 50" -> sold the same 10
        if side == "BUY" and qty is not None:
            shared_qty = qty
        intent.actions.append(TradeAction(side=side, price=_num(price_m.group(1)), quantity=qty,
                                          trade_date=parse_date(clause, today) or shared_date))
    return intent if intent.actions else None
