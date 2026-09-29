"""
The research package every model receives for one stock (multi-model analysis).

It is built only from data AiTrading actually holds — the deterministic engine's
indicators, the news feeds, the market overview — and stored with a timestamp and
a content hash, so every analysis can be traced to the exact data it saw and the
same package can be replayed to a future model. Missing values are written as
UNKNOWN, never estimated.

`facts` is a flat dict of verified values. The EvidenceEngine checks model claims
against it, and the learning layer stores it as the feature snapshot of the
prediction.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

UNKNOWN = "UNKNOWN"


@dataclass
class Headline:
    ref: str              # N1, N2 ... (models cite these ids)
    title: str
    source: str
    published: str
    sentiment: int        # keyword sentiment -1/0/+1
    ai_impact: Optional[int] = None   # the background model's earlier read (-2..+2), labelled as such
    scope: str = "stock"  # stock | market


@dataclass
class ResearchPackage:
    symbol: str
    name: str
    sector: Optional[str]
    collected_at: str                 # UTC ISO time the package was assembled
    data_timestamp: Optional[str]     # time of the last price bar used
    data_source: str
    facts: dict = field(default_factory=dict)
    headlines: list[Headline] = field(default_factory=list)
    market: dict = field(default_factory=dict)
    engine: dict = field(default_factory=dict)

    def headline(self, ref: str) -> Optional[Headline]:
        return next((h for h in self.headlines if h.ref.upper() == ref.upper()), None)

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol, "name": self.name, "sector": self.sector,
            "collected_at": self.collected_at, "data_timestamp": self.data_timestamp,
            "data_source": self.data_source, "facts": self.facts,
            "headlines": [h.__dict__ for h in self.headlines], "market": self.market, "engine": self.engine,
        }

    def content_hash(self) -> str:
        """Hash of the data only (not the collection time), so an unchanged package is recognised and reused."""
        body = {k: v for k, v in self.to_json().items() if k != "collected_at"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _r(value, digits: int = 2):
    return round(float(value), digits) if value is not None else None


def build_package(item, overview: Optional[dict], news_cache: list[dict], data_source: str,
                  top_sectors: Optional[list[str]] = None, now: Optional[datetime] = None) -> ResearchPackage:
    """Assemble the package from a RankedStock, the market overview and the cached news (no fetching here)."""
    t = item.technical
    plan = item.plan
    overview = overview or {}
    nifty = overview.get("nifty") or {}
    flows = overview.get("fii_dii") or {}

    facts = {
        "price": _r(t.close), "prev_close": _r(t.prev_close), "change_pct": _r(t.change_pct),
        "ema_20": _r(t.ema_20), "ema_50": _r(t.ema_50), "ema_200": _r(t.ema_200),
        "sma_20": _r(getattr(t, "sma_20", None)), "sma_50": _r(getattr(t, "sma_50", None)),
        "vwap_20": _r(getattr(t, "vwap_20", None)),
        "rsi": _r(t.rsi, 1), "macd": _r(t.macd, 3), "macd_signal": _r(t.macd_signal, 3), "macd_hist": _r(t.macd_hist, 3),
        "atr": _r(t.atr), "atr_pct": _r(t.atr_pct),
        "support": _r(t.support), "resistance": _r(t.resistance),
        "high_52w": _r(t.high_52w), "low_52w": _r(t.low_52w),
        "return_20d": _r(t.return_20d), "return_60d": _r(t.return_60d),
        "relative_strength_20d": _r(t.relative_strength_20d),
        "volume_ratio": _r(t.volume_ratio),
        "breakout": bool(getattr(t, "breakout", False)) if t.close is not None else None,
        "breakdown": bool(getattr(t, "breakdown", False)) if t.close is not None else None,
        "trend": t.trend if t.trend != "unknown" else None,
        "setups": list(t.setups or []),
        "technical_score": _r(t.technical_score, 1),
        "sector": item.sector,
        "sector_leading": (item.sector in (top_sectors or [])) if item.sector and top_sectors is not None else None,
        "market_regime": overview.get("regime"),
        "regimes": list(overview.get("regimes") or []),
        "nifty_trend": nifty.get("trend"),
        "nifty_change_pct": _r(overview.get("indices") and next(
            (i["change_pct"] for i in overview["indices"] if i.get("name") == "NIFTY 50"), None)),
        "vix": _r(overview.get("vix")),
        "fii_net_cr": _r(flows.get("fii_activity")),
        "dii_net_cr": _r(flows.get("dii_activity")),
    }

    headlines: list[Headline] = []
    reads = {r["title"]: r for r in (item.sentiment.get("ai_reads") or [])}
    for h in (item.sentiment.get("headlines") or [])[:5]:
        read = reads.get(h["title"])
        headlines.append(Headline(ref="", title=h["title"], source=h.get("source") or UNKNOWN,
                                  published=h.get("published") or UNKNOWN, sentiment=int(h.get("sentiment") or 0),
                                  ai_impact=read["impact"] if read else None, scope="stock"))
    seen = {h.title for h in headlines}
    moving = [n for n in news_cache if n.get("market_moving") and n["title"] not in seen][:4]
    for n in moving:
        headlines.append(Headline(ref="", title=n["title"], source=n.get("source") or UNKNOWN,
                                  published=n.get("published") or UNKNOWN, sentiment=int(n.get("sentiment") or 0),
                                  scope="market"))
    for i, h in enumerate(headlines, 1):
        h.ref = f"N{i}"
    facts["stock_news_count"] = sum(1 for h in headlines if h.scope == "stock")
    facts["stock_news_net_sentiment"] = sum(h.sentiment for h in headlines if h.scope == "stock")

    engine = {
        "action": plan.action if plan else None, "strategy": item.strategy,
        "entry_low": _r(plan.entry_low) if plan else None, "entry_high": _r(plan.entry_high) if plan else None,
        "stop_loss": _r(plan.stop_loss) if plan else None, "target": _r(plan.target) if plan else None,
        "risk_reward": _r(plan.risk_reward) if plan else None,
        "conviction": _r(item.conviction_score, 1),
        "backtest": {name: {"trades": s.signals, "win_rate": _r(s.win_rate, 1)}
                     for name, s in (t.backtest or {}).items() if s.signals and s.win_rate is not None},
    }
    return ResearchPackage(
        symbol=item.symbol, name=item.name, sector=item.sector,
        collected_at=(now or datetime.utcnow()).isoformat(timespec="seconds") + "Z",
        data_timestamp=t.last_bar_time, data_source=data_source,
        facts=facts, headlines=headlines,
        market={"summary": overview.get("summary"), "as_of": overview.get("as_of")}, engine=engine,
    )


def _v(value, suffix: str = "") -> str:
    if value is None or value == [] or value == "":
        return UNKNOWN
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return f"{value}{suffix}"


def render(package: ResearchPackage) -> str:
    """The package as the plain-text block every model receives (identical for all models)."""
    f, e = package.facts, package.engine
    lines = [
        f"DATA (price bar {_v(package.data_timestamp)}; values marked UNKNOWN are not available — do not guess them)",
        f"Price {_v(f['price'])} ({_v(f['change_pct'], '%')} today, previous close {_v(f['prev_close'])})",
        f"Trend {_v(f['trend'])}; setups detected: {_v(f['setups']) if f['setups'] else 'none'}",
        f"EMA20 {_v(f['ema_20'])} | EMA50 {_v(f['ema_50'])} | EMA200 {_v(f['ema_200'])} | "
        f"SMA20 {_v(f['sma_20'])} | SMA50 {_v(f['sma_50'])} | 20-day VWAP (daily bars) {_v(f['vwap_20'])}",
        f"RSI(14) {_v(f['rsi'])} | MACD {_v(f['macd'])} vs signal {_v(f['macd_signal'])} (hist {_v(f['macd_hist'])})",
        f"ATR(14) {_v(f['atr'])} ({_v(f['atr_pct'], '% of price')})",
        f"Support (prior 20-day low) {_v(f['support'])} | Resistance (prior 20-day high) {_v(f['resistance'])} | "
        f"52-week range {_v(f['low_52w'])}-{_v(f['high_52w'])}",
        f"Breakout above resistance on heavy volume: {_v(f['breakout'])} | Breakdown below support on heavy volume: "
        f"{_v(f['breakdown'])}",
        f"Volume {_v(f['volume_ratio'], 'x')} its 20-day average (today's partial volume projected to a full session)",
        f"Returns: 20-day {_v(f['return_20d'], '%')}, 60-day {_v(f['return_60d'], '%')}; vs NIFTY over 20 days "
        f"{_v(f['relative_strength_20d'], '%')}",
        f"Sector {_v(f['sector'])}{' (a leading sector today)' if f.get('sector_leading') else ''}",
        "",
        f"MARKET: regime {_v(f['market_regime'])} ({_v(f['regimes'])}); NIFTY trend {_v(f['nifty_trend'])}, "
        f"today {_v(f['nifty_change_pct'], '%')}; India VIX {_v(f['vix'])}; FII net {_v(f['fii_net_cr'], ' cr')}, "
        f"DII net {_v(f['dii_net_cr'], ' cr')}",
        f"Market summary: {package.market.get('summary') or UNKNOWN}",
        "",
        "NEWS (cite by id; only these headlines exist in the data):",
    ]
    if package.headlines:
        for h in package.headlines:
            read = f"; earlier AI read {h.ai_impact:+d}" if h.ai_impact is not None else ""
            lines.append(f"[{h.ref}] ({'this stock' if h.scope == 'stock' else 'market-wide'}, {h.source}, "
                         f"{h.published[:16] or UNKNOWN}{read}) {h.title}")
    else:
        lines.append("none — no headlines mention this stock and no market-moving headlines")
    lines += [
        "",
        f"RULE-BASED ENGINE (AiTrading's deterministic plan, for reference): {_v(e['action'])} "
        f"({_v(e['strategy'])}), entry {_v(e['entry_low'])}-{_v(e['entry_high'])}, stop {_v(e['stop_loss'])}, "
        f"target {_v(e['target'])}, conviction {_v(e['conviction'])}/100",
    ]
    if e.get("backtest"):
        lines.append("Back-test of setups on this stock's own history: " + "; ".join(
            f"{k} {v['trades']} trades, {v['win_rate']}% wins" for k, v in e["backtest"].items()))
    return "\n".join(lines)
