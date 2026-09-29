"""
EvidenceEngine: checks what each model *claims* against the data AiTrading holds.

Models cite claims from a fixed vocabulary (CLAIM_RULES) so they can be verified
exactly; anything outside it is kept as MODEL_INTERPRETATION and never counts as
evidence. News claims must cite a headline id (N1, N2 …) that exists in the
research package, otherwise they are flagged as news the data does not contain.

Everything here is pure (no I/O) so it is fully unit-tested. The same factor
definitions (`present_factors`) are used by the learning layer, so "breakout" means
the same thing when a model claims it, when it is verified, and when its
historical success rate is measured.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# Information categories (spec: never store an interpretation as a fact)
FACT = "FACT"
MODEL_INTERPRETATION = "MODEL_INTERPRETATION"
HISTORICAL_PATTERN = "HISTORICAL_PATTERN"
PREDICTION = "PREDICTION"
ASSUMPTION = "ASSUMPTION"

# Verification statuses
SUPPORTED = "SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
UNKNOWN = "UNKNOWN"              # the data needed to check it is missing
UNVERIFIABLE = "UNVERIFIABLE"    # not a checkable claim (outside the vocabulary / tone not confirmable)

# Thresholds shared by verification and the historical factor study
RSI_OVERBOUGHT, RSI_OVERSOLD, RSI_STRONG_LOW, RSI_WEAK = 70.0, 30.0, 55.0, 45.0
VOLUME_HIGH, VOLUME_LOW = 1.5, 0.8
NEAR_LEVEL_PCT = 2.0
NEAR_52W_PCT = 5.0
HIGH_VOL_ATR_PCT, HIGH_VIX = 3.0, 20.0


def _num(facts: dict, key: str) -> Optional[float]:
    v = facts.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _cmp(facts, a, b, above=True):
    x, y = _num(facts, a), _num(facts, b)
    if x is None or y is None:
        return None
    return x > y if above else x < y


def _within(facts, key, pct_above_ok=True):
    price, level = _num(facts, "price"), _num(facts, key)
    if price is None or level is None or level == 0:
        return None
    return abs(price - level) / level * 100 <= NEAR_LEVEL_PCT


def _rsi(pred):
    return lambda f: None if _num(f, "rsi") is None else pred(_num(f, "rsi"))


def _vr(pred):
    return lambda f: None if _num(f, "volume_ratio") is None else pred(_num(f, "volume_ratio"))


def _trend(name):
    return lambda f: None if f.get("trend") is None else f.get("trend") == name


def _regime(name):
    return lambda f: None if not f.get("regimes") else name in f["regimes"]


def _flow(key, positive):
    return lambda f: None if _num(f, key) is None else (_num(f, key) > 0 if positive else _num(f, key) < 0)


def _near_52w(high: bool):
    def check(f):
        price, level = _num(f, "price"), _num(f, "high_52w" if high else "low_52w")
        if price is None or level is None:
            return None
        return price >= level * (1 - NEAR_52W_PCT / 100) if high else price <= level * (1 + NEAR_52W_PCT / 100)
    return check


def _high_volatility(f):
    atr, vix = _num(f, "atr_pct"), _num(f, "vix")
    if atr is None and vix is None:
        return None
    return bool((atr is not None and atr >= HIGH_VOL_ATR_PCT) or (vix is not None and vix >= HIGH_VIX))


@dataclass(frozen=True)
class ClaimRule:
    bias: str                                   # bullish | bearish | risk | neutral
    check: Callable[[dict], Optional[bool]]
    describe: Callable[[dict], str]             # the actual data, in words
    label: str
    category: str = FACT


def _show(*keys):
    return lambda f: ", ".join(f"{k}={f.get(k) if f.get(k) is not None else 'UNKNOWN'}" for k in keys)


CLAIM_RULES: dict[str, ClaimRule] = {
    "breakout": ClaimRule("bullish", lambda f: _cmp(f, "price", "resistance"), _show("price", "resistance"),
                          "Price above the prior 20-day high"),
    "breakdown": ClaimRule("bearish", lambda f: _cmp(f, "price", "support", above=False), _show("price", "support"),
                           "Price below the prior 20-day low"),
    "near_resistance": ClaimRule("bearish", lambda f: _within(f, "resistance"), _show("price", "resistance"),
                                 "Price close to resistance"),
    "near_support": ClaimRule("bullish", lambda f: _within(f, "support"), _show("price", "support"),
                              "Price close to support"),
    "above_ema20": ClaimRule("bullish", lambda f: _cmp(f, "price", "ema_20"), _show("price", "ema_20"), "Price above EMA 20"),
    "below_ema20": ClaimRule("bearish", lambda f: _cmp(f, "price", "ema_20", False), _show("price", "ema_20"), "Price below EMA 20"),
    "above_ema50": ClaimRule("bullish", lambda f: _cmp(f, "price", "ema_50"), _show("price", "ema_50"), "Price above EMA 50"),
    "below_ema50": ClaimRule("bearish", lambda f: _cmp(f, "price", "ema_50", False), _show("price", "ema_50"), "Price below EMA 50"),
    "above_ema200": ClaimRule("bullish", lambda f: _cmp(f, "price", "ema_200"), _show("price", "ema_200"), "Price above EMA 200"),
    "below_ema200": ClaimRule("bearish", lambda f: _cmp(f, "price", "ema_200", False), _show("price", "ema_200"), "Price below EMA 200"),
    "above_vwap": ClaimRule("bullish", lambda f: _cmp(f, "price", "vwap_20"), _show("price", "vwap_20"), "Price above 20-day VWAP"),
    "below_vwap": ClaimRule("bearish", lambda f: _cmp(f, "price", "vwap_20", False), _show("price", "vwap_20"), "Price below 20-day VWAP"),
    "uptrend": ClaimRule("bullish", _trend("uptrend"), _show("trend"), "Uptrend (EMA structure)"),
    "downtrend": ClaimRule("bearish", _trend("downtrend"), _show("trend"), "Downtrend (EMA structure)"),
    "rsi_overbought": ClaimRule("bearish", _rsi(lambda r: r > RSI_OVERBOUGHT), _show("rsi"), "RSI overbought (>70)"),
    "rsi_oversold": ClaimRule("bullish", _rsi(lambda r: r < RSI_OVERSOLD), _show("rsi"), "RSI oversold (<30)"),
    "rsi_strong": ClaimRule("bullish", _rsi(lambda r: RSI_STRONG_LOW <= r <= RSI_OVERBOUGHT), _show("rsi"), "RSI strong (55-70)"),
    "rsi_weak": ClaimRule("bearish", _rsi(lambda r: r < RSI_WEAK), _show("rsi"), "RSI weak (<45)"),
    "macd_bullish": ClaimRule("bullish", lambda f: _cmp(f, "macd", "macd_signal"), _show("macd", "macd_signal"), "MACD above signal"),
    "macd_bearish": ClaimRule("bearish", lambda f: _cmp(f, "macd", "macd_signal", False), _show("macd", "macd_signal"), "MACD below signal"),
    "volume_high": ClaimRule("bullish", _vr(lambda v: v >= VOLUME_HIGH), _show("volume_ratio"), "Volume >= 1.5x average"),
    "volume_low": ClaimRule("bearish", _vr(lambda v: v < VOLUME_LOW), _show("volume_ratio"), "Volume < 0.8x average"),
    "outperforming_nifty": ClaimRule("bullish", lambda f: None if _num(f, "relative_strength_20d") is None
                                     else _num(f, "relative_strength_20d") > 0, _show("relative_strength_20d"),
                                     "Outperforming NIFTY over 20 days"),
    "underperforming_nifty": ClaimRule("bearish", lambda f: None if _num(f, "relative_strength_20d") is None
                                       else _num(f, "relative_strength_20d") < 0, _show("relative_strength_20d"),
                                       "Underperforming NIFTY over 20 days"),
    "near_52w_high": ClaimRule("bullish", _near_52w(True), _show("price", "high_52w"), "Within 5% of the 52-week high"),
    "near_52w_low": ClaimRule("bearish", _near_52w(False), _show("price", "low_52w"), "Within 5% of the 52-week low"),
    "high_volatility": ClaimRule("risk", _high_volatility, _show("atr_pct", "vix"), "High volatility (ATR >= 3% or VIX >= 20)"),
    "market_bullish": ClaimRule("bullish", _regime("BULLISH"), _show("regimes"), "Market regime bullish"),
    "market_bearish": ClaimRule("bearish", _regime("BEARISH"), _show("regimes"), "Market regime bearish"),
    "fii_buying": ClaimRule("bullish", _flow("fii_net_cr", True), _show("fii_net_cr"), "FIIs net buyers"),
    "fii_selling": ClaimRule("bearish", _flow("fii_net_cr", False), _show("fii_net_cr"), "FIIs net sellers"),
    "sector_strong": ClaimRule("bullish", lambda f: f.get("sector_leading"), _show("sector", "sector_leading"),
                               "Sector among today's leaders"),
    "no_news": ClaimRule("neutral", lambda f: None if f.get("stock_news_count") is None else f["stock_news_count"] == 0,
                         _show("stock_news_count"), "No stock-specific news"),
    "backtest_favorable": ClaimRule("bullish", lambda f: f.get("_backtest_favorable"), _show("_backtest_detail"),
                                    "Current setup historically worked on this stock", HISTORICAL_PATTERN),
}
NEWS_CLAIMS = {"positive_news": "bullish", "negative_news": "bearish"}
ALL_CLAIM_KEYS = sorted(list(CLAIM_RULES) + list(NEWS_CLAIMS))

# Factors used by the learning layer (true/false straight from data; no model involved)
FACTOR_KEYS = [k for k, r in CLAIM_RULES.items() if r.bias in ("bullish", "bearish") and r.category == FACT
               and not k.startswith(("market_", "fii_", "sector_"))]


def enrich_facts(facts: dict, engine: dict) -> dict:
    """Adds derived facts the vocabulary needs (e.g. whether the current setup's back-test is favourable)."""
    facts = dict(facts)
    bt = (engine or {}).get("backtest") or {}
    strategy = (engine or {}).get("strategy")
    stats = bt.get(strategy) if strategy else None
    if stats and stats.get("trades", 0) >= 5 and stats.get("win_rate") is not None:
        facts["_backtest_favorable"] = stats["win_rate"] >= 55
        facts["_backtest_detail"] = f"{strategy}: {stats['trades']} trades, {stats['win_rate']}% wins"
    else:
        facts["_backtest_favorable"] = None
        facts["_backtest_detail"] = None
    return facts


def present_factors(facts: dict) -> list[str]:
    """Factor keys that are true in the data right now."""
    return [k for k in FACTOR_KEYS if CLAIM_RULES[k].check(facts) is True]


@dataclass
class Claim:
    key: str
    evidence: str = ""
    confidence: Optional[float] = None       # the model's stated confidence in this claim (0-100)
    ref: Optional[str] = None                # news id


@dataclass
class ClaimCheck:
    key: str
    label: str
    bias: str
    status: str
    category: str
    actual: str
    model_evidence: str
    model_confidence: Optional[float] = None
    news: Optional[dict] = None
    flag: Optional[str] = None

    def to_json(self) -> dict:
        return self.__dict__.copy()


@dataclass
class EvidenceReport:
    checks: list[ClaimCheck] = field(default_factory=list)
    evidence_score: Optional[float] = None     # 0-1 share of checkable claims the data supports
    supported: int = 0
    not_supported: int = 0
    unknown: int = 0
    unverifiable: int = 0
    fabricated_news: int = 0
    aligned_support: int = 0                   # supported claims that point the same way as the call
    flags: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)   # everything classified FACT / INTERPRETATION / PREDICTION …

    def to_json(self) -> dict:
        return {"checks": [c.to_json() for c in self.checks], "evidence_score": self.evidence_score,
                "supported": self.supported, "not_supported": self.not_supported, "unknown": self.unknown,
                "unverifiable": self.unverifiable, "fabricated_news": self.fabricated_news,
                "aligned_support": self.aligned_support, "flags": self.flags, "items": self.items}


_CLAIM_LINE = re.compile(r"^\W*CLAIM\s*\d*\s*[:=]\s*(.+)$", re.IGNORECASE)
_NEWS_REF = re.compile(r"\bN(\d{1,2})\b")


def parse_claims(text: str) -> list[Claim]:
    """'CLAIM: key[:N2] | evidence | confidence' lines -> Claims (unknown keys are kept, marked later)."""
    claims = []
    for line in (text or "").splitlines():
        m = _CLAIM_LINE.match(line.strip())
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split("|")]
        head = parts[0].lower().strip(" *`\"'")
        key, _, ref = head.partition(":")
        key = re.sub(r"[^a-z0-9_]", "_", key.strip()).strip("_")
        evidence = parts[1] if len(parts) > 1 else ""
        conf = None
        if len(parts) > 2:
            n = re.search(r"\d+(\.\d+)?", parts[2])
            if n:
                conf = float(n.group())
                conf = conf * 100 if conf <= 1 and "." in n.group() else conf
                conf = max(0.0, min(100.0, conf))
        ref_match = _NEWS_REF.search(ref.upper()) or _NEWS_REF.search(evidence.upper())
        claims.append(Claim(key=key, evidence=evidence[:300], confidence=conf,
                            ref=f"N{ref_match.group(1)}" if ref_match else None))
    return claims[:12]


def _call_bias(recommendation: Optional[str]) -> Optional[str]:
    return {"BUY": "bullish", "SELL": "bearish", "AVOID": "bearish"}.get((recommendation or "").upper())


def verify(claims: list[Claim], facts: dict, headlines: list[dict], recommendation: Optional[str] = None,
           news_text: str = "", predictions: Optional[dict] = None, interpretation: str = "") -> EvidenceReport:
    """
    Check every claim against `facts` (flat verified values) and `headlines`
    (dicts with ref/title/source/published/sentiment/ai_impact/scope).
    """
    report = EvidenceReport()
    call = _call_bias(recommendation)
    by_ref = {h["ref"].upper(): h for h in headlines}
    weight_ok = weight_all = 0.0

    for claim in claims:
        conf_w = (claim.confidence or 60.0) / 100
        if claim.key in NEWS_CLAIMS:
            bias = NEWS_CLAIMS[claim.key]
            h = by_ref.get((claim.ref or "").upper())
            if h is None:
                check = ClaimCheck(claim.key, "Positive news" if bias == "bullish" else "Negative news", bias,
                                   NOT_SUPPORTED, MODEL_INTERPRETATION,
                                   "no such headline in the research data" if claim.ref else "no headline cited",
                                   claim.evidence, claim.confidence, None, "news_not_in_data")
                report.fabricated_news += 1
            else:
                tone = h.get("ai_impact") if h.get("ai_impact") not in (None, 0) else h.get("sentiment", 0)
                wanted = 1 if bias == "bullish" else -1
                if tone and (tone > 0) == (wanted > 0):
                    status = SUPPORTED
                elif tone:
                    status = NOT_SUPPORTED
                else:
                    status = UNVERIFIABLE   # the headline exists, but its tone can't be confirmed from the data
                news = {"exists": True, "ref": h["ref"], "title": h["title"], "source": h.get("source"),
                        "published": h.get("published"), "relevance": h.get("scope"),
                        "sentiment": h.get("sentiment"), "earlier_ai_read": h.get("ai_impact")}
                check = ClaimCheck(claim.key, "Positive news" if bias == "bullish" else "Negative news", bias, status,
                                   FACT if status == SUPPORTED else MODEL_INTERPRETATION,
                                   f"{h['ref']}: {h['title'][:120]}", claim.evidence, claim.confidence, news)
        elif claim.key in CLAIM_RULES:
            rule = CLAIM_RULES[claim.key]
            result = rule.check(facts)
            status = UNKNOWN if result is None else SUPPORTED if result else NOT_SUPPORTED
            category = ASSUMPTION if result is None else rule.category if result else MODEL_INTERPRETATION
            check = ClaimCheck(claim.key, rule.label, rule.bias, status, category, rule.describe(facts),
                               claim.evidence, claim.confidence)
        else:
            check = ClaimCheck(claim.key, claim.key.replace("_", " "), "neutral", UNVERIFIABLE, MODEL_INTERPRETATION,
                               "not a checkable claim", claim.evidence, claim.confidence)
        report.checks.append(check)

        if check.status == SUPPORTED:
            report.supported += 1
            weight_ok += conf_w
            weight_all += conf_w
            if call and check.bias == call:
                report.aligned_support += 1
        elif check.status == NOT_SUPPORTED:
            report.not_supported += 1
            weight_all += conf_w
        elif check.status == UNKNOWN:
            report.unknown += 1
        else:
            report.unverifiable += 1

    if weight_all > 0:
        report.evidence_score = round(weight_ok / weight_all, 3)

    # Free-text news mentioned when the data has no stock news at all
    stock_news = [h for h in headlines if h.get("scope") == "stock"]
    text = (news_text or "").lower()
    if not stock_news and text and not re.search(r"\b(no|none|not|nothing|absent|lack)\b", text):
        report.flags.append("Mentions stock news, but the research data has no headline about this stock")
        report.fabricated_news += 1
    if report.fabricated_news:
        report.flags.append(f"{report.fabricated_news} news claim(s) not found in the research data")
    if report.not_supported:
        report.flags.append(f"{report.not_supported} claim(s) contradicted by the data")

    # Classify everything the model said
    for c in report.checks:
        report.items.append({"category": c.category, "text": f"{c.label}: {c.actual}", "status": c.status,
                             "source": "data" if c.category == FACT else "model"})
    for k, v in (predictions or {}).items():
        if v is not None:
            report.items.append({"category": PREDICTION, "text": f"{k}: {v}", "status": UNVERIFIABLE, "source": "model"})
    if interpretation:
        report.items.append({"category": MODEL_INTERPRETATION, "text": interpretation[:400],
                             "status": UNVERIFIABLE, "source": "model"})
    return report


def check_levels(recommendation: Optional[str], price: Optional[float], atr: Optional[float],
                 entry: Optional[float], target: Optional[float], stop: Optional[float]) -> list[str]:
    """Sanity checks on the model's price levels (they are predictions, but must be coherent with the data)."""
    issues = []
    if price is None:
        return issues
    rec = (recommendation or "").upper()
    if rec == "BUY":
        if stop is not None and stop >= price:
            issues.append(f"stop {stop} is not below the current price {price}")
        if target is not None and target <= price:
            issues.append(f"target {target} is not above the current price {price}")
    for name, level in (("entry", entry), ("target", target), ("stop", stop)):
        if level is not None and atr and abs(level - price) > 8 * atr:
            issues.append(f"{name} {level} is more than 8 ATR from the price — implausible for {price}")
    return issues
