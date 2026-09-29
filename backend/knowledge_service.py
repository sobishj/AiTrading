"""
Knowledge and learning layer for multi-model analysis.

Knowledge here is evidence, not opinion:
- pattern_stats: how often each factor (breakout, volume_high, rsi_overbought …, and
  pairs of them) was followed by the move it implies, measured
    * "history": on the tracked stocks' past daily bars (a statistical study — no LLM),
    * "live":    on graded live analyses (the exact data the models saw).
  A factor only influences the consensus after MIN_PATTERN_SAMPLES observations.
- model reliability: each model's graded record, overall and in the same market
  regime / setup, including how often it was right when it disagreed with the others.
- retrieval for prompts: graded past analyses of the stock (labelled with the model
  that wrote them and what actually happened) and the measured factor history.
  Ungraded opinions of other models are never shown, to avoid AI-to-AI contamination.

Plain SQL lookups are used rather than vector search: what is retrieved is keyed by
stock, factor and regime, so exact matches beat semantic similarity here.
"""
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import combinations
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import or_
from sqlalchemy.orm import Session

from evidence_engine import CLAIM_RULES, present_factors
from models import AIConsensus, AIPrediction, PatternStat, ResearchContext, Stock, tracked_stock_filter
from utils.logger import get_logger

logger = get_logger(__name__)

HORIZON = 5
HISTORY_DAYS = 1100
HISTORY_WARMUP = 220
HISTORY_REFRESH_DAYS = 6
MIN_PATTERN_SAMPLES = 30
MIN_CONTEXT_SAMPLES = 10


def trend_regime(close: float, ema20: Optional[float], ema50: Optional[float]) -> str:
    if ema20 is None or ema50 is None or pd.isna(ema20) or pd.isna(ema50):
        return "UNKNOWN"
    if close > ema20 > ema50:
        return "BULLISH"
    if close < ema20 < ema50:
        return "BEARISH"
    return "SIDEWAYS"


def primary_regime(regimes: Optional[str]) -> Optional[str]:
    """First (trend) label of a stored 'BULLISH,HIGH_VOLATILITY' string."""
    return regimes.split(",")[0] if regimes else None


class _Counter:
    def __init__(self):
        self.n = self.ok = 0
        self.ret = 0.0
        self.models: set[str] = set()


def _bias(key: str) -> str:
    return CLAIM_RULES[key].bias


def accumulate(counters: dict, factors: list[str], regime: str, ret: float, models: Iterable[str] = ()) -> None:
    """Count one observation: base rate, each factor, and same-direction factor pairs (ALL + this regime)."""
    ret = float(ret)
    up = int(ret > 0)
    for reg in {"ALL", regime}:
        base = counters[("ALL", "bullish", reg)]
        base.n += 1
        base.ok += up
        base.ret += ret
        singles = [(k, _bias(k)) for k in factors]
        pairs = [(f"{a}+{b}", _bias(a)) for a, b in combinations(sorted(factors), 2) if _bias(a) == _bias(b)]
        for pattern, bias in singles + (pairs if reg == "ALL" else []):
            c = counters[(pattern, bias, reg)]
            c.n += 1
            c.ok += up if bias == "bullish" else int(ret < 0)
            c.ret += ret
            c.models.update(models)


def _facts_from_row(row) -> dict:
    g = lambda k: None if pd.isna(row.get(k)) else float(row.get(k))  # noqa: E731
    close, e20, e50 = g("close"), g("ema_20"), g("ema_50")
    return {
        "price": close, "resistance": g("prior_high_20"), "support": g("prior_low_20"),
        "ema_20": e20, "ema_50": e50, "ema_200": g("ema_200"), "vwap_20": g("vwap_20"),
        "rsi": g("rsi"), "macd": g("macd"), "macd_signal": g("macd_signal"), "volume_ratio": g("volume_ratio"),
        "high_52w": g("high_52w"), "low_52w": g("low_52w"),
        "trend": None if e20 is None or e50 is None else
        ("uptrend" if close > e20 > e50 else "downtrend" if close < e20 < e50 else "sideways"),
    }


def study_frame(df: pd.DataFrame, nifty_regime: dict, counters: dict) -> int:
    """Walk one stock's indicator frame; every bar with a known 5-day future adds one observation."""
    closes = df["close"].to_numpy()
    dates = pd.to_datetime(df["date"]).dt.date.to_numpy() if "date" in df.columns else [None] * len(df)
    seen = 0
    for i in range(HISTORY_WARMUP, len(df) - HORIZON):
        row = df.iloc[i]
        facts = _facts_from_row(row)
        ret = (closes[i + HORIZON] / closes[i] - 1) * 100
        accumulate(counters, present_factors(facts), nifty_regime.get(dates[i], "UNKNOWN"), ret)
        seen += 1
    return seen


def _save_counters(db: Session, counters: dict, source: str) -> int:
    db.query(PatternStat).filter(PatternStat.source == source).delete()
    now = datetime.utcnow()
    for (pattern, bias, regime), c in counters.items():
        if c.n == 0:
            continue
        db.add(PatternStat(pattern=pattern, bias=bias, regime=regime, timeframe=f"{HORIZON}d", source=source,
                           occurrences=int(c.n), successes=int(c.ok), failures=int(c.n - c.ok),
                           total_return_pct=round(float(c.ret), 2),
                           source_models=",".join(sorted(c.models)) or None, first_observed=now, last_observed=now))
    db.commit()
    return len(counters)


class KnowledgeService:
    # ------------------------------------------------------------------
    # Pattern statistics
    # ------------------------------------------------------------------
    async def history_study(self, db: Session, force: bool = False) -> dict:
        """Measure factor success rates on ~3 years of the tracked stocks' daily bars (weekly refresh)."""
        from analysis_service import analysis_service
        from market_service import market_service

        latest = (db.query(PatternStat.last_observed).filter(PatternStat.source == "history")
                  .order_by(PatternStat.last_observed.desc()).first())
        if latest and not force and datetime.utcnow() - latest[0] < timedelta(days=HISTORY_REFRESH_DAYS):
            return {"skipped": True, "last_run": latest[0].isoformat()}

        nifty = await market_service.get_candles_for_symbol("^NSEI", days=HISTORY_DAYS)
        nifty_regime: dict = {}
        if not nifty.empty and "date" in nifty.columns:
            nf = analysis_service.indicator_frame(nifty)
            for d, c, e20, e50 in zip(pd.to_datetime(nf["date"]).dt.date, nf["close"], nf["ema_20"], nf["ema_50"]):
                nifty_regime[d] = trend_regime(c, e20, e50)

        stocks = db.query(Stock).filter(tracked_stock_filter()).all()
        counters: dict = defaultdict(_Counter)
        observations = 0
        for stock in stocks:
            candles = await market_service.get_candles_for_symbol(stock.symbol, days=HISTORY_DAYS)
            if candles.empty or len(candles) < HISTORY_WARMUP + HORIZON + 10 or "date" not in candles.columns:
                continue
            df = analysis_service.indicator_frame(candles)
            observations += await asyncio.to_thread(study_frame, df, nifty_regime, counters)
        if observations == 0:
            return {"skipped": True, "reason": "no price history available"}
        rows = _save_counters(db, counters, "history")
        logger.info("Factor study: %d observations from %d stocks -> %d pattern rows", observations, len(stocks), rows)
        return {"observations": observations, "stocks": len(stocks), "patterns": rows}

    def update_live_stats(self, db: Session) -> int:
        """Rebuild live pattern stats from graded analyses (one observation per stock per day)."""
        rows = (db.query(AIPrediction, ResearchContext)
                .join(ResearchContext, AIPrediction.context_id == ResearchContext.id)
                .filter(AIPrediction.graded_at.isnot(None), AIPrediction.kind == "live",
                        AIPrediction.role.in_(("primary", "adhoc")), AIPrediction.model == "consensus")
                .order_by(AIPrediction.prediction_date.desc(), AIPrediction.id.desc()).all())
        counters: dict = defaultdict(_Counter)
        seen = set()
        for pred, ctx in rows:
            key = (pred.stock_id, pred.prediction_date)
            if key in seen:
                continue
            seen.add(key)
            facts = json.loads(ctx.context_json).get("facts", {})
            models = [m.model for m in db.query(AIPrediction.model).filter(AIPrediction.consensus_id == pred.consensus_id,
                                                                          AIPrediction.role == "member")]
            accumulate(counters, present_factors(facts), primary_regime(pred.market_regime) or "UNKNOWN",
                       float(pred.actual_return_pct), models)
        if counters:
            _save_counters(db, counters, "live")
        return len(seen)

    def factor_stats_for(self, db: Session, factors: list[str], regime: Optional[str]) -> list[dict]:
        """Best available measured stats for each factor present now (live beats history once it has enough data)."""
        if not factors:
            return []
        rows = db.query(PatternStat).filter(PatternStat.pattern.in_(factors + ["ALL"])).all()
        index = {(r.pattern, r.regime, r.source): r for r in rows}
        out = []
        for f in factors:
            chosen = None
            for source in ("live", "history"):
                for reg in ([regime] if regime else []) + ["ALL"]:
                    r = index.get((f, reg, source))
                    if r and r.occurrences >= MIN_PATTERN_SAMPLES:
                        chosen = (r, reg, source)
                        break
                if chosen:
                    break
            if not chosen:
                continue
            r, reg, source = chosen
            base = index.get(("ALL", reg, source))
            out.append({"pattern": f, "bias": r.bias, "occurrences": r.occurrences, "successes": r.successes,
                        "regime": reg, "source": source,
                        "base_up_rate": round(base.successes / base.occurrences * 100, 1) if base and base.occurrences else None,
                        "avg_return_pct": round(float(r.total_return_pct) / r.occurrences, 2)})
        return out

    def pattern_table(self, db: Session, source: Optional[str] = None, min_n: int = MIN_PATTERN_SAMPLES,
                      limit: int = 60) -> list[dict]:
        q = db.query(PatternStat).filter(PatternStat.occurrences >= min_n)
        if source:
            q = q.filter(PatternStat.source == source)
        base = {(r.regime, r.source): r for r in db.query(PatternStat).filter(PatternStat.pattern == "ALL")}
        out = []
        for r in q.all():
            if r.pattern == "ALL":
                continue
            b = base.get((r.regime, r.source))
            base_rate = (b.successes / b.occurrences * 100) if b and b.occurrences else None
            if base_rate is not None and r.bias == "bearish":
                base_rate = 100 - base_rate
            rate = r.successes / r.occurrences * 100
            out.append({"pattern": r.pattern, "bias": r.bias, "regime": r.regime, "source": r.source,
                        "occurrences": r.occurrences, "successes": r.successes, "failures": r.failures,
                        "success_rate": round(rate, 1),
                        "base_rate": round(base_rate, 1) if base_rate is not None else None,
                        "edge": round(rate - base_rate, 1) if base_rate is not None else None,
                        "avg_return_pct": round(float(r.total_return_pct) / r.occurrences, 2),
                        "source_models": r.source_models, "timeframe": r.timeframe,
                        "last_observed": r.last_observed.isoformat() if r.last_observed else None})
        out.sort(key=lambda x: (abs(x["edge"] or 0) * (x["occurrences"] ** 0.5)), reverse=True)
        return out[:limit]

    # ------------------------------------------------------------------
    # Model reliability and performance
    # ------------------------------------------------------------------
    @staticmethod
    def _member_rows(db: Session, profile_id: Optional[int] = None, model: Optional[str] = None):
        q = (db.query(AIPrediction, Stock).join(Stock, AIPrediction.stock_id == Stock.id)
             .filter(AIPrediction.graded_at.isnot(None), AIPrediction.kind == "live", AIPrediction.role == "member"))
        if profile_id is not None:
            q = q.filter(AIPrediction.profile_id == profile_id)
        elif model is not None:
            q = q.filter(AIPrediction.model == model)
        return q.all()

    def model_reliability(self, db: Session, profile_id: int, regime: Optional[str], setup: Optional[str]) -> dict:
        rows = [p for p, _ in self._member_rows(db, profile_id=profile_id)]
        stats = {"n": len(rows), "hits": sum(1 for p in rows if p.correct)}
        candidates = [
            (lambda p: primary_regime(p.market_regime) == regime and p.strategy == setup,
             f"{regime} markets, {setup} setups"),
            (lambda p: primary_regime(p.market_regime) == regime, f"{regime} markets"),
            (lambda p: p.strategy == setup, f"{setup} setups"),
        ]
        for pred, label in candidates:
            subset = [p for p in rows if pred(p)]
            if len(subset) >= MIN_CONTEXT_SAMPLES:
                stats.update(context_n=len(subset), context_hits=sum(1 for p in subset if p.correct),
                             context_label=label)
                break
        return stats

    def model_performance(self, db: Session) -> list[dict]:
        """Per-model graded record with breakdowns by regime, setup, sector, dissent, and calibration."""
        groups: dict = defaultdict(list)
        for p, stock in self._member_rows(db):
            groups[(p.profile_id, p.model)].append((p, stock))
        consensus_rows = (db.query(AIPrediction, Stock).join(Stock, AIPrediction.stock_id == Stock.id)
                          .filter(AIPrediction.graded_at.isnot(None), AIPrediction.model == "consensus",
                                  AIPrediction.kind == "live").all())
        if consensus_rows:
            groups[(None, "consensus")] = consensus_rows
        consensus_signal = {c.consensus_id: c.recommendation for c, _ in consensus_rows if c.consensus_id}
        for c in db.query(AIConsensus.id, AIConsensus.signal).all():
            consensus_signal.setdefault(c.id, c.signal)

        def rate(items):
            return round(sum(1 for p, _ in items if p.correct) / len(items) * 100, 1) if items else None

        def breakdown(items, key):
            b = defaultdict(list)
            for p, s in items:
                b[key(p, s) or "UNKNOWN"].append((p, s))
            return [{"label": k, "n": len(v), "hit_rate": rate(v)} for k, v in sorted(b.items(), key=lambda x: -len(x[1]))]

        out = []
        for (profile_id, model), items in groups.items():
            n = len(items)
            conf = [float(p.confidence) for p, _ in items if p.confidence is not None]
            right_conf = [float(p.confidence) for p, _ in items if p.confidence is not None and p.correct]
            wrong_conf = [float(p.confidence) for p, _ in items if p.confidence is not None and not p.correct]
            ev = [float(p.evidence_score) for p, _ in items if p.evidence_score is not None]
            brier = sum(((float(p.probability_up) / 100) - (1.0 if float(p.actual_return_pct) > 0 else 0.0)) ** 2
                        for p, _ in items) / n
            dissent = [(p, s) for p, s in items if p.role == "member" and p.consensus_id in consensus_signal
                       and p.recommendation and _bucket(p.recommendation) != _bucket(consensus_signal[p.consensus_id])]
            pnl = [float(p.outcome_pnl_pct) for p, _ in items if p.outcome_pnl_pct is not None]
            out.append({
                "profile_id": profile_id, "model": model, "graded": n, "hit_rate": rate(items),
                "brier": round(brier, 3),
                "stated_confidence_avg": round(sum(conf) / len(conf), 1) if conf else None,
                "confidence_when_right": round(sum(right_conf) / len(right_conf), 1) if right_conf else None,
                "confidence_when_wrong": round(sum(wrong_conf) / len(wrong_conf), 1) if wrong_conf else None,
                "evidence_score_avg": round(sum(ev) / len(ev) * 100, 1) if ev else None,
                "avg_pnl_pct": round(sum(pnl) / len(pnl), 2) if pnl else None,
                "target_hit_rate": _share(items, "target_hit"), "stop_hit_rate": _share(items, "stop_hit"),
                "dissent": {"n": len(dissent), "hit_rate": rate(dissent)},
                "by_regime": breakdown(items, lambda p, s: primary_regime(p.market_regime)),
                "by_setup": breakdown(items, lambda p, s: p.strategy),
                "by_sector": breakdown(items, lambda p, s: s.sector)[:8],
            })
        out.sort(key=lambda r: (r["model"] != "consensus", -(r["graded"])))
        return out

    # ------------------------------------------------------------------
    # Retrieval for prompts
    # ------------------------------------------------------------------
    def knowledge_block(self, db: Session, stock_id: int, factors: list[str], regime: Optional[str]) -> str:
        lines = []
        past = (db.query(AIPrediction)
                .filter(AIPrediction.stock_id == stock_id, AIPrediction.kind == "live",
                        AIPrediction.graded_at.isnot(None),
                        or_(AIPrediction.role == "member", AIPrediction.model == "consensus"))
                .order_by(AIPrediction.prediction_date.desc(), AIPrediction.id.desc()).limit(6).all())
        if past:
            lines.append("Graded past analyses of this stock (source model, what it said, what actually happened):")
            for p in past:
                said = p.recommendation or p.direction
                conf = f", stated confidence {float(p.confidence):.0f}" if p.confidence is not None else ""
                lines.append(f"- {p.prediction_date:%d %b %Y} | Source: {p.model}{conf} | said {said} "
                             f"({float(p.probability_up):.0f}% up) | actual {float(p.actual_return_pct):+.1f}% over "
                             f"{p.horizon_days} days → {'RIGHT' if p.correct else 'WRONG'}")
        stats = self.factor_stats_for(db, factors, regime)
        if stats:
            lines.append("Measured history of the factors present now (real outcomes over 5 days, not opinions):")
            for s in stats[:8]:
                rate = s["successes"] / s["occurrences"] * 100
                base = s["base_up_rate"] if s["bias"] == "bullish" else (100 - s["base_up_rate"]) if s["base_up_rate"] is not None else None
                move = "rose" if s["bias"] == "bullish" else "fell"
                lines.append(f"- {s['pattern']}: price {move} in {rate:.0f}% of {s['occurrences']} cases"
                             + (f" (vs {base:.0f}% for any day)" if base is not None else "")
                             + f" [{s['source']}, {s['regime']} regime]")
        dissent = [m for m in self.model_performance(db) if m["model"] != "consensus" and m["dissent"]["n"] >= 5]
        if dissent:
            lines.append("How often each model was right when it disagreed with the combined view: " + "; ".join(
                f"{m['model']} {m['dissent']['hit_rate']:.0f}% of {m['dissent']['n']}" for m in dissent))
        return "\n".join(lines) or "none yet — no graded analyses or measured factor history for this situation."

    # ------------------------------------------------------------------
    # Training data export
    # ------------------------------------------------------------------
    def training_records(self, db: Session, graded_only: bool = True) -> Iterable[dict]:
        """
        One record per model analysis, carrying everything needed to reconstruct it without the
        original model: the exact prompt and data package, the model's answer, the evidence checks,
        the combined view, and the measured outcome.
        """
        q = (db.query(AIPrediction, Stock, ResearchContext)
             .join(Stock, AIPrediction.stock_id == Stock.id)
             .join(ResearchContext, AIPrediction.context_id == ResearchContext.id)
             .filter(AIPrediction.kind == "live"))
        if graded_only:
            q = q.filter(AIPrediction.graded_at.isnot(None))
        consensus_cache: dict = {}
        for p, stock, ctx in q.order_by(AIPrediction.id).yield_per(200):
            cons = None
            if p.consensus_id:
                if p.consensus_id not in consensus_cache:
                    c = db.get(AIConsensus, p.consensus_id)
                    consensus_cache[p.consensus_id] = {
                        "signal": c.signal, "probability_up": float(c.probability_up),
                        "confidence": float(c.confidence), "votes": json.loads(c.votes_json),
                        "scores": json.loads(c.scores_json)} if c else None
                cons = consensus_cache[p.consensus_id]
            context = json.loads(ctx.context_json)
            yield {
                "id": p.id, "symbol": stock.symbol, "sector": stock.sector,
                "prediction_date": p.prediction_date.isoformat(), "created_at": p.created_at.isoformat(),
                "data_timestamp": ctx.data_timestamp.isoformat() if ctx.data_timestamp else context.get("data_timestamp"),
                "role": p.role, "model": p.model, "market_regime": p.market_regime, "setup": p.strategy,
                "prompt": ctx.prompt_text if p.role == "member" else None,
                "research": context, "factors_present": present_factors(context.get("facts", {})),
                "response": p.raw_response,
                "analysis": json.loads(p.analysis_json) if p.analysis_json else None,
                "prediction": {"recommendation": p.recommendation, "direction": p.direction,
                               "probability_up": _f(p.probability_up), "stated_confidence": _f(p.confidence),
                               "expected_move_pct": _f(p.expected_move_pct), "entry": _f(p.entry),
                               "target": _f(p.target), "stop_loss": _f(p.stop_loss), "timeframe": p.timeframe},
                "evidence_score": _f(p.evidence_score), "consensus": cons,
                "outcome": None if p.graded_at is None else {
                    "horizon_days": p.horizon_days, "return_pct": _f(p.actual_return_pct),
                    "direction": p.actual_direction, "correct": p.correct,
                    "max_favorable_pct": _f(p.max_favorable_pct), "max_adverse_pct": _f(p.max_adverse_pct),
                    "target_hit": p.target_hit, "stop_hit": p.stop_hit, "pnl_pct": _f(p.outcome_pnl_pct),
                    "graded_at": p.graded_at.isoformat()},
            }


def _f(v):
    return float(v) if v is not None else None


def _bucket(rec: Optional[str]) -> str:
    from consensus_engine import bucket
    return bucket(rec)


def _share(items, attr) -> Optional[float]:
    vals = [getattr(p, attr) for p, _ in items if getattr(p, attr) is not None]
    return round(sum(1 for v in vals if v) / len(vals) * 100, 1) if vals else None


knowledge_service = KnowledgeService()
