"""
Your trading style, learned from the trades you actually record (Holdings buys/sells and
Zerodha uploads) — never from what a model says.

Every entry is replayed against real daily bars: what the chart looked like when you
bought (trend, breakout, dip or chase, distance from the 52-week high, RSI), whether you
followed or went against the AI's view that day, and what the price did afterwards. Every
sale is checked for what happened next (sold too early?) and how deep the trade went
against you first. Aggregates become plain statements, each shown with its sample size;
a pattern is only stated once enough trades back it.

The profile is given to every model (chat and each model in multi-model analysis) to
tailor entries, targets, stops, timeframe and warnings to you. It is deliberately NOT used
to change forecasts of where a price will go — your preferences don't move the market.
"""
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from evidence_engine import CLAIM_RULES, present_factors
from models import (AIPrediction, Position, PositionTransaction, Recommendation, Stock, TraderProfile,
                    TradeHistory)
from utils.logger import get_logger

logger = get_logger(__name__)

MIN_FOR_PATTERN = 3          # trades before a pattern is stated at all
SOLID_PATTERN = 10           # below this, patterns are labelled "early sign"
FORWARD_BARS = (5, 10)
LOOKBACK_FOR_INDICATORS = 320
DIP_PCT, CHASE_PCT = -3.0, 5.0
EARLY_EXIT_PCT = 3.0
PRICE_TOLERANCE = 0.03       # a recorded price more than 3% outside that day's real low-high is treated as a typo


@dataclass
class Entry:
    symbol: str
    name: str
    sector: Optional[str]
    stock_id: int
    day: date
    price: float
    quantity: float
    source: str
    factors: list[str] = field(default_factory=list)
    style: list[str] = field(default_factory=list)       # dip_buy / chase / near_day_high …
    rsi: Optional[float] = None
    ret_5d_before: Optional[float] = None
    fwd: dict = field(default_factory=dict)              # {5: %, 10: %}
    mfe_10: Optional[float] = None
    mae_10: Optional[float] = None
    ai_view: Optional[str] = None                        # BUY / WAIT / AVOID / up / down …
    ai_alignment: Optional[str] = None                   # followed / against / none
    price_issue: Optional[str] = None                    # set when the price doesn't match that day's real range


@dataclass
class Exit:
    symbol: str
    day: date
    price: float
    quantity: float
    pnl_pct: Optional[float]
    pnl_amount: Optional[float]
    holding_days: Optional[int]
    after_10d_pct: Optional[float] = None                # price move in the 10 bars after the sale
    mae_before_exit: Optional[float] = None
    price_issue: Optional[str] = None


# ----------------------------------------------------------------------
# Pure aggregation (unit-tested)
# ----------------------------------------------------------------------
def _pct(n: int, d: int) -> Optional[float]:
    return round(n / d * 100, 1) if d else None


def _avg(values: list) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def _label(n: int) -> str:
    return "" if n >= SOLID_PATTERN else " (early sign)"


def summarize(entries: list[Entry], exits: list[Exit], open_positions: list[dict]) -> dict:
    """Turn replayed entries/exits into measured stats and plain statements (with sample sizes)."""
    issues = [e.price_issue for e in entries if e.price_issue] + [x.price_issue for x in exits if x.price_issue]
    entries = [e for e in entries if not e.price_issue]
    exits = [x for x in exits if not x.price_issue]
    closed = [x for x in exits if x.pnl_pct is not None]
    wins = [x for x in closed if x.pnl_pct > 0]
    losses = [x for x in closed if x.pnl_pct <= 0]
    avg_win, avg_loss = _avg([x.pnl_pct for x in wins]), _avg([x.pnl_pct for x in losses])
    invested = [e.price * e.quantity for e in entries]

    style_counts = Counter(s for e in entries for s in e.style)
    factor_counts = Counter(f for e in entries for f in e.factors)
    n_entries = len(entries)

    # Your own results by entry factor (10-day forward return after your buy)
    by_factor: dict = defaultdict(list)
    for e in entries:
        if e.fwd.get(10) is not None:
            for f in e.factors + e.style:
                by_factor[f].append(e.fwd[10])
    personal_factors = sorted(
        ({"factor": f, "n": len(v), "avg_10d_pct": _avg(v), "rose_pct": _pct(sum(1 for r in v if r > 0), len(v))}
         for f, v in by_factor.items()), key=lambda r: -r["n"])

    followed = [e.fwd.get(10) for e in entries if e.ai_alignment == "followed" and e.fwd.get(10) is not None]
    against = [e.fwd.get(10) for e in entries if e.ai_alignment == "against" and e.fwd.get(10) is not None]

    early_exits = [x for x in exits if x.after_10d_pct is not None and x.after_10d_pct >= EARLY_EXIT_PCT]
    measured_exits = [x for x in exits if x.after_10d_pct is not None]

    stats = {
        "entries": n_entries, "closed_trades": len(closed), "open_positions": len(open_positions),
        "win_rate": _pct(len(wins), len(closed)), "avg_win_pct": avg_win, "avg_loss_pct": avg_loss,
        "payoff_ratio": round(abs(avg_win / avg_loss), 2) if avg_win and avg_loss else None,
        "expectancy_pct": _avg([x.pnl_pct for x in closed]),
        "avg_hold_days_winners": _avg([x.holding_days for x in wins]),
        "avg_hold_days_losers": _avg([x.holding_days for x in losses]),
        "typical_position_inr": round(median(invested)) if invested else None,
        "largest_position_inr": round(max(invested)) if invested else None,
        "worst_trade_pct": min((x.pnl_pct for x in closed), default=None),
        "sectors": Counter(e.sector or "Unknown" for e in entries).most_common(5),
        "entry_style": {k: {"n": v, "share_pct": _pct(v, n_entries)} for k, v in style_counts.most_common()},
        "entry_factors": {k: {"n": v, "share_pct": _pct(v, n_entries)} for k, v in factor_counts.most_common(8)},
        "followed_ai": {"n": len(followed), "avg_10d_pct": _avg(followed)},
        "against_ai": {"n": len(against), "avg_10d_pct": _avg(against)},
        "early_exits": {"n": len(early_exits), "of": len(measured_exits)},
        "personal_factors": personal_factors[:12],
    }

    notes: list[str] = []
    for issue in issues:
        notes.append(f"Excluded from learning until corrected: {issue}")
    if n_entries < MIN_FOR_PATTERN:
        notes.append(f"Only {n_entries} buy(s) recorded so far — style patterns need at least {MIN_FOR_PATTERN}; "
                     f"until then, only the facts of each trade are used.")
    else:
        for key, text in (("uptrend", "buys stocks already in an uptrend"),
                          ("near_52w_high", "buys near the 52-week high"),
                          ("breakout", "buys breakouts above the 20-day high")):
            n = factor_counts.get(key, 0)
            if n / n_entries >= 0.6:
                notes.append(f"Usually {text}: {n} of {n_entries} buys{_label(n_entries)}.")
        dips, chases = style_counts.get("dip_buy", 0), style_counts.get("chase", 0)
        if dips / n_entries >= 0.5:
            notes.append(f"Buys dips: {dips} of {n_entries} buys came after a 5-day fall of 3%+{_label(n_entries)}.")
        if chases / n_entries >= 0.4:
            notes.append(f"Chases strength: {chases} of {n_entries} buys came after a 5-day rise of 5%+{_label(n_entries)}.")
        if style_counts.get("near_day_high", 0) / n_entries >= 0.5:
            notes.append(f"Often buys near the day's high: {style_counts['near_day_high']} of {n_entries}{_label(n_entries)}.")
    if len(closed) >= MIN_FOR_PATTERN:
        hw, hl = stats["avg_hold_days_winners"], stats["avg_hold_days_losers"]
        if hw is not None and hl is not None and hl > hw * 1.5 and len(losses) >= 2:
            notes.append(f"Holds losers longer than winners: {hl:.0f} vs {hw:.0f} days on average{_label(len(closed))}.")
        if stats["payoff_ratio"] is not None and stats["payoff_ratio"] < 1 and len(wins) >= 2 and len(losses) >= 2:
            notes.append(f"Average loss ({avg_loss:+.1f}%) is bigger than average win ({avg_win:+.1f}%) — "
                         f"tighter stops or letting winners run would help{_label(len(closed))}.")
    if len(measured_exits) >= MIN_FOR_PATTERN and len(early_exits) / len(measured_exits) >= 0.5:
        notes.append(f"Tends to sell early: after {len(early_exits)} of {len(measured_exits)} sales the price rose "
                     f"another {EARLY_EXIT_PCT:.0f}%+ within 10 days{_label(len(measured_exits))}.")
    if len(followed) >= MIN_FOR_PATTERN and len(against) >= MIN_FOR_PATTERN:
        notes.append(f"Buys that followed the AI's view averaged {_avg(followed):+.1f}% after 10 days (n={len(followed)}); "
                     f"buys against it averaged {_avg(against):+.1f}% (n={len(against)}).")
    for pf in personal_factors:
        if pf["n"] >= MIN_FOR_PATTERN and pf["avg_10d_pct"] is not None and abs(pf["avg_10d_pct"]) >= 2:
            label = CLAIM_RULES[pf["factor"]].label if pf["factor"] in CLAIM_RULES else pf["factor"].replace("_", " ")
            notes.append(f"Your buys with '{label}' averaged {pf['avg_10d_pct']:+.1f}% after 10 days "
                         f"({pf['rose_pct']:.0f}% rose, n={pf['n']}){_label(pf['n'])}.")
    stats["excluded_trades"] = len(issues)
    return {"stats": stats, "style_notes": notes, "data_issues": issues}


def prompt_block(profile: Optional[dict], symbol: Optional[str] = None) -> str:
    """Compact text for model prompts (facts with sample sizes; empty profile says so)."""
    if not profile or not profile.get("stats", {}).get("entries"):
        return "No trades recorded yet — no personal style to account for."
    s = profile["stats"]
    lines = [f"Recorded: {s['entries']} buy(s), {s['closed_trades']} closed trade(s), {s['open_positions']} open holding(s)"
             + (f"; win rate {s['win_rate']:.0f}%" if s.get("win_rate") is not None else "")
             + (f"; typical position ₹{s['typical_position_inr']:,}" if s.get("typical_position_inr") else "")
             + (f"; avg hold {s['avg_hold_days_winners']:.0f} days (winners) / {s['avg_hold_days_losers']:.0f} (losers)"
                if s.get("avg_hold_days_winners") is not None and s.get("avg_hold_days_losers") is not None else "")]
    if s.get("sectors"):
        lines.append("Sectors traded: " + ", ".join(f"{name} ({n})" for name, n in s["sectors"]))
    lines += [f"- {n}" for n in profile.get("style_notes", [])]
    if symbol:
        mine = [h for h in profile.get("holdings", []) if h["symbol"] == symbol]
        for h in mine:
            lines.append(f"The trader currently holds {h['quantity']:g} {symbol} at an average of ₹{h['avg_price']:.2f}"
                         + (f" (stop {h['stop_loss']}, target {h['target']})" if h.get("stop_loss") or h.get("target") else ""))
        past = [e for e in profile.get("entries", []) if e["symbol"] == symbol][-3:]
        for e in past:
            fwd = e["fwd"].get("10") if isinstance(e["fwd"], dict) else None
            lines.append(f"Earlier buy of {symbol} on {e['day']} at ₹{e['price']:.2f}"
                         + (f": 10 days later {fwd:+.1f}%" if fwd is not None else ""))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Replay against real bars
# ----------------------------------------------------------------------
def _row_at(df: pd.DataFrame, day: date) -> Optional[int]:
    dates = pd.to_datetime(df["date"]).dt.date
    idx = [i for i, d in enumerate(dates) if d <= day]
    return idx[-1] if idx else None


def price_mismatch(df: pd.DataFrame, day: date, price: float, what: str) -> Optional[str]:
    """A reason string when the recorded price is outside that day's real trading range (else None)."""
    dates = pd.to_datetime(df["date"]).dt.date
    rows = df[dates == day]
    if rows.empty:
        return None                    # no bar that day (holiday / data gap) — can't check, don't accuse
    low, high = float(rows.iloc[-1]["low"]), float(rows.iloc[-1]["high"])
    if low * (1 - PRICE_TOLERANCE) <= price <= high * (1 + PRICE_TOLERANCE):
        return None
    return (f"{what} on {day:%d %b %Y} at Rs {price:,.2f}, but it traded Rs {low:,.2f}-{high:,.2f} that day "
            f"(typo? fix it in Holdings)")


def replay_entry(entry: Entry, df: pd.DataFrame) -> None:
    """Fill the entry's chart context and forward outcome from the indicator frame (real bars only)."""
    from knowledge_service import _facts_from_row

    entry.price_issue = price_mismatch(df, entry.day, entry.price, f"{entry.symbol} buy")
    i = _row_at(df, entry.day)
    if i is None:
        return
    row = df.iloc[i]
    facts = _facts_from_row(row)
    facts["price"] = entry.price
    entry.factors = present_factors(facts)
    entry.rsi = round(float(row["rsi"]), 1) if not pd.isna(row.get("rsi")) else None
    closes, highs, lows = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float)
    if i >= 5:
        entry.ret_5d_before = round((float(closes.iloc[i]) / float(closes.iloc[i - 5]) - 1) * 100, 2)
        if entry.ret_5d_before <= DIP_PCT:
            entry.style.append("dip_buy")
        elif entry.ret_5d_before >= CHASE_PCT:
            entry.style.append("chase")
    day_range = float(highs.iloc[i]) - float(lows.iloc[i])
    if pd.to_datetime(df["date"]).dt.date.iloc[i] == entry.day and day_range > 0:
        position_in_range = (entry.price - float(lows.iloc[i])) / day_range
        if position_in_range >= 0.8:
            entry.style.append("near_day_high")
        elif position_in_range <= 0.2:
            entry.style.append("near_day_low")
    for n in FORWARD_BARS:
        if i + n < len(df):
            entry.fwd[n] = round((float(closes.iloc[i + n]) / entry.price - 1) * 100, 2)
    window = df.iloc[i + 1: i + 11]
    if len(window):
        entry.mfe_10 = round((float(window["high"].max()) / entry.price - 1) * 100, 2)
        entry.mae_10 = round((float(window["low"].min()) / entry.price - 1) * 100, 2)


def replay_exit(exit_: Exit, df: pd.DataFrame, entry_day: Optional[date]) -> None:
    exit_.price_issue = price_mismatch(df, exit_.day, exit_.price, f"{exit_.symbol} sale")
    i = _row_at(df, exit_.day)
    if i is None:
        return
    if i + 10 < len(df):
        exit_.after_10d_pct = round((float(df["close"].iloc[i + 10]) / exit_.price - 1) * 100, 2)
    if entry_day is not None:
        j = _row_at(df, entry_day)
        if j is not None and j <= i and exit_.pnl_pct is not None:
            entry_price = exit_.price / (1 + exit_.pnl_pct / 100)
            low = float(df["low"].iloc[j: i + 1].astype(float).min())
            exit_.mae_before_exit = round((low / entry_price - 1) * 100, 2)


def ai_view_on(db: Session, stock_id: int, day: date) -> Optional[str]:
    """The AI's view of the stock when you bought: consensus/forecast that day, else the engine's recommendation."""
    p = (db.query(AIPrediction).filter(AIPrediction.stock_id == stock_id, AIPrediction.kind == "live",
                                       AIPrediction.role.in_(("primary", "adhoc")),
                                       AIPrediction.prediction_date <= day,
                                       AIPrediction.prediction_date >= day - timedelta(days=3))
         .order_by(AIPrediction.prediction_date.desc(), AIPrediction.id.desc()).first())
    if p is not None:
        return p.recommendation or p.direction
    r = (db.query(Recommendation).filter(Recommendation.stock_id == stock_id,
                                         Recommendation.timestamp <= datetime.combine(day, datetime.max.time()),
                                         Recommendation.timestamp >= datetime.combine(day - timedelta(days=3), datetime.min.time()))
         .order_by(Recommendation.timestamp.desc()).first())
    return r.action if r is not None else None


def alignment_of(view: Optional[str]) -> str:
    if not view:
        return "none"
    v = view.upper()
    if v in ("BUY", "UP"):
        return "followed"
    if v in ("SELL", "AVOID", "DOWN"):
        return "against"
    return "neutral"


class TraderProfileService:
    async def refresh(self, db: Session) -> dict:
        from analysis_service import analysis_service
        from market_service import market_service

        entries: list[Entry] = []
        exits: list[tuple[Exit, Optional[date], int]] = []

        for tx, pos, stock in (db.query(PositionTransaction, Position, Stock)
                               .join(Position, PositionTransaction.position_id == Position.id)
                               .join(Stock, Position.stock_id == Stock.id)
                               .order_by(PositionTransaction.trade_date, PositionTransaction.id)):
            if tx.side == "BUY":
                entries.append(Entry(stock.symbol, stock.name, stock.sector, stock.id, tx.trade_date, float(tx.price),
                                     float(tx.quantity), "manual"))
            else:
                qty = float(tx.quantity)
                pnl = float(tx.realized_pnl) if tx.realized_pnl is not None else None
                avg_cost = float(tx.price) - pnl / qty if pnl is not None and qty else None
                pct = round((float(tx.price) / avg_cost - 1) * 100, 2) if avg_cost else None
                exits.append((Exit(stock.symbol, tx.trade_date, float(tx.price), qty, pct, pnl,
                                   (tx.trade_date - pos.opened_on).days), pos.opened_on, stock.id))

        for t, stock in (db.query(TradeHistory, Stock).join(Stock, TradeHistory.stock_id == Stock.id)
                         .filter(TradeHistory.source == "zerodha")):
            entries.append(Entry(stock.symbol, stock.name, stock.sector, stock.id, t.execution_date,
                                 float(t.entry_price), float(t.quantity or 0), "zerodha"))
            if t.exit_price is not None and t.exit_date is not None:
                pct = round((float(t.exit_price) / float(t.entry_price) - 1) * 100, 2)
                exits.append((Exit(stock.symbol, t.exit_date, float(t.exit_price), float(t.quantity or 0), pct,
                                   float(t.profit_loss) if t.profit_loss is not None else None,
                                   (t.exit_date - t.execution_date).days), t.execution_date, stock.id))

        frames: dict[str, pd.DataFrame] = {}
        earliest = min([e.day for e in entries] + [x.day for x, _, _ in exits], default=date.today())
        days = (date.today() - earliest).days + LOOKBACK_FOR_INDICATORS
        for symbol in {e.symbol for e in entries} | {x.symbol for x, _, _ in exits}:
            try:
                candles = await market_service.get_candles_for_symbol(symbol, days=days)
                if not candles.empty and "date" in candles.columns and len(candles) >= 30:
                    frames[symbol] = analysis_service.indicator_frame(candles)
            except Exception as exc:  # noqa: BLE001  (missing data -> that trade's context stays UNKNOWN)
                logger.warning("Trader profile: no bars for %s (%s)", symbol, exc)

        for e in entries:
            if e.symbol in frames:
                replay_entry(e, frames[e.symbol])
            e.ai_view = ai_view_on(db, e.stock_id, e.day)
            e.ai_alignment = alignment_of(e.ai_view)
        for x, entry_day, _ in exits:
            if x.symbol in frames:
                replay_exit(x, frames[x.symbol], entry_day)

        holdings = [{"symbol": p.stock.symbol, "quantity": float(p.quantity), "avg_price": float(p.avg_price),
                     "stop_loss": float(p.stop_loss) if p.stop_loss is not None else None,
                     "target": float(p.target) if p.target is not None else None, "opened_on": p.opened_on.isoformat()}
                    for p in db.query(Position).filter(Position.status == "open")]
        profile = summarize(entries, [x for x, _, _ in exits], holdings)
        profile.update({
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "holdings": holdings,
            "entries": [{"symbol": e.symbol, "day": e.day.isoformat(), "price": e.price, "quantity": e.quantity,
                         "source": e.source, "factors": e.factors, "style": e.style, "rsi": e.rsi,
                         "ret_5d_before": e.ret_5d_before, "fwd": {str(k): v for k, v in e.fwd.items()},
                         "mfe_10": e.mfe_10, "mae_10": e.mae_10, "ai_view": e.ai_view, "ai_alignment": e.ai_alignment,
                         "price_issue": e.price_issue}
                        for e in entries],
            "exits": [{"symbol": x.symbol, "day": x.day.isoformat(), "price": x.price, "quantity": x.quantity,
                       "pnl_pct": x.pnl_pct, "holding_days": x.holding_days, "after_10d_pct": x.after_10d_pct,
                       "mae_before_exit": x.mae_before_exit, "price_issue": x.price_issue} for x, _, _ in exits],
        })
        row = db.get(TraderProfile, 1) or TraderProfile(id=1)
        row.profile_json = json.dumps(profile, default=str)
        row.trades_analyzed = len(entries) + len(exits)
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        logger.info("Trader profile updated: %d buys, %d sells, %d style notes", len(entries), len(exits),
                    len(profile["style_notes"]))
        return profile

    @staticmethod
    def get(db: Session) -> Optional[dict]:
        row = db.get(TraderProfile, 1)
        return json.loads(row.profile_json) if row and row.profile_json else None

    def block(self, db: Session, symbol: Optional[str] = None) -> str:
        return prompt_block(self.get(db), symbol)


trader_profile_service = TraderProfileService()


async def refresh_in_background() -> None:
    """Rebuild the profile after a trade is recorded (own DB session; failures never affect the trade)."""
    from database import db_session
    try:
        with db_session() as db:
            await trader_profile_service.refresh(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Trader profile refresh failed: %s", exc)
