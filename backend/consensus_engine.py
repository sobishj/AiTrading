"""
ConsensusEngine: combines several models' analyses by *evidence*, not by vote count.

For each model:
  reliability = its measured hit rate in similar situations (same market regime /
                setup when there are enough graded outcomes, else overall),
                shrunk toward 50% so a few lucky calls don't dominate
  evidence    = the share of its checkable claims the data supports
                (EvidenceEngine), with a penalty for news that isn't in the data
  weight      = reliability x evidence
A model's *stated* confidence is recorded and shown, but carries no weight.

The models' weighted probability is then blended with the measured historical
success rate of the factors that are actually present in the data (pattern_stats:
counted from real outcomes only). Votes are reported ("2 BUY / 1 SELL") so
disagreement is never hidden, but they are not used to decide.

Pure functions only; nothing here guarantees an outcome.
"""
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

PRIOR_STRENGTH = 10            # pseudo-observations at 50% when shrinking a hit rate
MIN_CONTEXT_SAMPLES = 10       # graded outcomes needed before the context-specific hit rate is used
MIN_PATTERN_SAMPLES = 30       # historical observations needed before a factor's success rate counts
HISTORY_WEIGHT = 0.4           # share of the final probability from measured factor history (when available)
BUY_THRESHOLD, SELL_THRESHOLD = 58.0, 42.0
MIN_EVIDENCE_FOR_SIGNAL = 0.4  # weighted evidence support below this -> HOLD ("evidence too weak")
PROB_FLOOR, PROB_CEILING = 10.0, 90.0


def bucket(recommendation: Optional[str]) -> str:
    rec = (recommendation or "").upper()
    return {"BUY": "BUY", "SELL": "SELL", "AVOID": "SELL"}.get(rec, "HOLD")


def shrunk_rate(hits: int, n: int) -> float:
    return (hits + PRIOR_STRENGTH * 0.5) / (n + PRIOR_STRENGTH)


def reliability(stats: dict) -> tuple[float, str]:
    """(weight 0.25-2.0, explanation) from graded outcomes; 1.0 = no record yet."""
    n, hits = int(stats.get("n") or 0), int(stats.get("hits") or 0)
    cn, ch = int(stats.get("context_n") or 0), int(stats.get("context_hits") or 0)
    if cn >= MIN_CONTEXT_SAMPLES:
        rate, why = shrunk_rate(ch, cn), f"{ch}/{cn} correct in {stats.get('context_label') or 'similar setups'}"
    elif n > 0:
        rate, why = shrunk_rate(hits, n), f"{hits}/{n} correct overall"
    else:
        return 1.0, "no graded outcomes yet (neutral weight)"
    return max(0.25, min(2.0, 1.0 + (rate - 0.5) * 4)), why


def evidence_weight(evidence: dict) -> tuple[float, str]:
    score = evidence.get("evidence_score")
    fabricated = int(evidence.get("fabricated_news") or 0)
    if score is None:
        w, why = 0.4, "no checkable claims"
    else:
        w, why = 0.25 + 0.75 * float(score), f"{evidence.get('supported', 0)} of " \
            f"{evidence.get('supported', 0) + evidence.get('not_supported', 0)} checkable claims supported by data"
    if fabricated:
        w *= 0.5 ** fabricated
        why += f"; {fabricated} news claim(s) not in the data"
    return max(0.05, w), why


@dataclass
class ConsensusResult:
    signal: str
    probability_up: float
    confidence: float                 # AiTrading's evidence confidence (not any model's stated confidence)
    votes: dict
    vote_text: str
    members: list[dict] = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    levels: dict = field(default_factory=dict)
    evidence_for: list[dict] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)
    historical: list[dict] = field(default_factory=list)
    disagreement: list[dict] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return self.__dict__.copy()


def _weighted_median(pairs: list[tuple[float, float]]) -> Optional[float]:
    pairs = sorted((v, w) for v, w in pairs if v is not None and w > 0)
    if not pairs:
        return None
    total, acc = sum(w for _, w in pairs), 0.0
    for value, w in pairs:
        acc += w
        if acc >= total / 2:
            return round(value, 2)
    return round(pairs[-1][0], 2)


def historical_probability(factor_stats: list[dict]) -> tuple[Optional[float], list[dict]]:
    """
    Probability-up implied by the measured history of the factors present now.
    Each factor contributes its edge over the base rate (averaged, not summed, because
    factors overlap). None when no factor has enough observations.
    """
    usable = [s for s in factor_stats if (s.get("occurrences") or 0) >= MIN_PATTERN_SAMPLES
              and s.get("base_up_rate") is not None]
    if not usable:
        return None, []
    shifts, lines = [], []
    base = usable[0]["base_up_rate"]
    for s in usable:
        rate = s["successes"] / s["occurrences"] * 100
        if s["bias"] == "bullish":
            shift = rate - s["base_up_rate"]
        else:
            shift = -(rate - (100 - s["base_up_rate"]))
        shifts.append(shift)
        lines.append({"pattern": s["pattern"], "bias": s["bias"], "occurrences": s["occurrences"],
                      "success_rate": round(rate, 1), "base_rate": round(s["base_up_rate"] if s["bias"] == "bullish"
                                                                          else 100 - s["base_up_rate"], 1),
                      "edge": round(shift if s["bias"] == "bullish" else -shift, 1), "source": s.get("source")})
    prob = base + sum(shifts) / len(shifts)
    return round(max(PROB_FLOOR, min(PROB_CEILING, prob)), 1), lines


def combine(members: list[dict], factor_stats: list[dict], facts: dict, engine: Optional[dict] = None) -> Optional[ConsensusResult]:
    """
    members: dicts with model, recommendation, probability_up, confidence, entry/target/stop_loss,
             evidence (EvidenceReport.to_json()), reliability_stats {n, hits, context_n, context_hits, context_label}.
    Only valid members (parsed answers) should be passed. Returns None when there are none.
    """
    if not members:
        return None
    votes = {"BUY": 0, "HOLD": 0, "SELL": 0}
    scored = []
    for m in members:
        b = bucket(m.get("recommendation"))
        votes[b] += 1
        rel, rel_why = reliability(m.get("reliability_stats") or {})
        ev, ev_why = evidence_weight(m.get("evidence") or {})
        weight = rel * ev
        scored.append({**m, "bucket": b, "weight": round(weight, 3), "reliability_weight": round(rel, 2),
                       "reliability_why": rel_why, "evidence_weight": round(ev, 2), "evidence_why": ev_why})

    total_w = sum(s["weight"] for s in scored)
    p_models = sum(s["weight"] * float(s["probability_up"]) for s in scored) / total_w
    p_hist, history_lines = historical_probability(factor_stats)
    p_final = p_models if p_hist is None else (1 - HISTORY_WEIGHT) * p_models + HISTORY_WEIGHT * p_hist
    p_final = round(max(PROB_FLOOR, min(PROB_CEILING, p_final)), 1)

    # Weighted evidence support across models, and weighted agreement with the resulting direction
    ev_scores = [(s["evidence"].get("evidence_score"), s["weight"]) for s in scored
                 if (s.get("evidence") or {}).get("evidence_score") is not None]
    evidence_avg = (sum(v * w for v, w in ev_scores) / sum(w for _, w in ev_scores)) if ev_scores else 0.0

    signal = "BUY" if p_final >= BUY_THRESHOLD else "SELL" if p_final <= SELL_THRESHOLD else "HOLD"
    reasoning = []
    if signal != "HOLD" and evidence_avg < MIN_EVIDENCE_FOR_SIGNAL:
        reasoning.append(f"Held back from {signal}: only {evidence_avg:.0%} of the models' checkable claims are "
                         f"supported by the data.")
        signal = "HOLD"
    agreement = sum(s["weight"] for s in scored if s["bucket"] == signal) / total_w
    decisiveness = abs(p_final - 50) / 40
    confidence = round(100 * (0.4 * evidence_avg + 0.3 * agreement + 0.3 * min(1.0, decisiveness)), 1)

    # Evidence and risks: verified facts only, deduplicated across models
    seen, evidence_for, risks = set(), [], []
    want = {"BUY": "bullish", "SELL": "bearish"}.get(signal)
    for s in scored:
        for c in (s.get("evidence") or {}).get("checks", []):
            if c["status"] != "SUPPORTED" or c["key"] in seen:
                continue
            seen.add(c["key"])
            entry = {"key": c["key"], "label": c["label"], "actual": c["actual"], "bias": c["bias"],
                     "cited_by": [x["model"] for x in scored
                                  if any(cc["key"] == c["key"] and cc["status"] == "SUPPORTED"
                                         for cc in (x.get("evidence") or {}).get("checks", []))]}
            if c["bias"] == "risk" or (want and c["bias"] not in (want, "neutral")):
                risks.append(entry)
            elif want is None or c["bias"] == want:
                evidence_for.append(entry)
            else:
                risks.append(entry)

    # Levels: weighted median of the models whose call matches the signal, sanity-checked
    same = [s for s in scored if s["bucket"] == signal and not s.get("level_issues")]
    levels = {"source": "models", "entry": _weighted_median([(s.get("entry"), s["weight"]) for s in same]),
              "target": _weighted_median([(s.get("target"), s["weight"]) for s in same]),
              "stop_loss": _weighted_median([(s.get("stop_loss"), s["weight"]) for s in same])}
    price = facts.get("price")
    if signal == "BUY" and price and not (levels["stop_loss"] and levels["target"]
                                          and levels["stop_loss"] < price < levels["target"]):
        e = engine or {}
        levels = {"source": "rule-based engine", "entry": e.get("entry_high"), "target": e.get("target"),
                  "stop_loss": e.get("stop_loss")}
    if signal != "BUY" and not any(levels.get(k) for k in ("entry", "target", "stop_loss")):
        levels = {"source": None, "entry": None, "target": None, "stop_loss": None}

    # Disagreement is preserved and explained
    majority = max(votes, key=lambda k: (votes[k], k == signal))
    disagreement = []
    for s in scored:
        if s["bucket"] != signal:
            checks = (s.get("evidence") or {}).get("checks", [])
            backed = [c["label"] for c in checks if c["status"] == "SUPPORTED"]
            refuted = [c["label"] for c in checks if c["status"] == "NOT_SUPPORTED"]
            disagreement.append({"model": s["model"], "recommendation": s.get("recommendation"),
                                 "reasoning": s.get("reasoning"), "supported": backed, "not_supported": refuted,
                                 "weight": s["weight"]})

    vote_text = " / ".join(f"{n} {k}" for k, n in votes.items() if n)
    reasoning.insert(0, f"Models: {vote_text} (shown for transparency; the signal comes from evidence-weighted "
                        f"probabilities, not the vote).")
    reasoning.append(f"Evidence-weighted model probability of a rise: {p_models:.0f}%"
                     + (f"; measured history of the factors present now: {p_hist:.0f}%" if p_hist is not None
                        else "; not enough measured history for these factors yet") + f" → {p_final:.0f}%.")
    if engine and engine.get("action") and bucket(engine["action"]) != signal:
        reasoning.append(f"Note: the rule-based engine says {engine['action']} ({engine.get('strategy')}); "
                         f"the trade plan still comes from the engine.")

    return ConsensusResult(
        signal=signal, probability_up=p_final, confidence=confidence, votes=votes, vote_text=vote_text,
        members=[{k: s.get(k) for k in ("model", "profile_id", "recommendation", "bucket", "probability_up",
                                         "confidence", "weight", "reliability_weight", "reliability_why",
                                         "evidence_weight", "evidence_why")} for s in scored],
        scores={"model_probability": round(p_models, 1), "history_probability": p_hist,
                "evidence_support": round(evidence_avg * 100, 1), "agreement": round(agreement * 100, 1),
                "majority_vote": majority},
        levels=levels, evidence_for=evidence_for, risks=risks, historical=history_lines,
        disagreement=disagreement, reasoning=reasoning,
    )
