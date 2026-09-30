"""Multi-model architecture: parsing, evidence checks, consensus, research package, credentials,
provider isolation (mocked providers) and outcome measurement — no DB, no network."""
import asyncio
import os
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import credential_store  # noqa: E402
from ai_analyst_service import trade_outcome  # noqa: E402
from ai_orchestrator import AIOrchestrator, ProfileSnapshot, parse_structured  # noqa: E402
from consensus_engine import bucket, combine, historical_probability, reliability  # noqa: E402
from evidence_engine import (NOT_SUPPORTED, SUPPORTED, UNKNOWN, UNVERIFIABLE, Claim, check_levels,  # noqa: E402
                             parse_claims, present_factors, verify)
from knowledge_service import _Counter, accumulate  # noqa: E402
from llm_providers import ChatResult, ProviderError, mask_key  # noqa: E402
from market_service import classify_regimes, is_market_moving  # noqa: E402
from research_context import UNKNOWN as R_UNKNOWN, build_package, render  # noqa: E402

FACTS = {"price": 1515.0, "resistance": 1500.0, "support": 1400.0, "ema_20": 1480.0, "ema_50": 1450.0,
         "ema_200": 1300.0, "vwap_20": 1490.0, "rsi": 74.0, "macd": 5.0, "macd_signal": 3.0,
         "volume_ratio": 2.3, "trend": "uptrend", "relative_strength_20d": 3.2, "high_52w": 1530.0,
         "low_52w": 1100.0, "atr": 30.0, "atr_pct": 2.0, "vix": 14.0, "regimes": ["BULLISH"],
         "stock_news_count": 1, "fii_net_cr": None}
HEADLINES = [{"ref": "N1", "title": "Acme wins large order", "source": "ET", "published": "2026-09-29T09:00",
              "sentiment": 1, "ai_impact": 2, "scope": "stock"},
             {"ref": "N2", "title": "RBI holds repo rate", "source": "Mint", "published": "2026-09-29T10:00",
              "sentiment": 0, "ai_impact": None, "scope": "market"}]

ANSWER = """RECOMMENDATION: BUY
DIRECTION: up
CONFIDENCE: 82
PROBABILITY_UP: 68
EXPECTED_MOVE: 2.4
ENTRY: 1510-1520
TARGET: 1560
STOP_LOSS: 1480
TIMEFRAME: 3-5 days
TECHNICAL: Breakout above resistance on heavy volume.
NEWS: Order win reported (N1).
RISKS: RSI elevated; market volatility
REASONING: Breakout with volume and supportive news.
CLAIM: breakout | price above resistance | 90
CLAIM: volume_high | 2.3x average | 85
CLAIM: positive_news:N1 | order win | 70
"""


# ----------------------------------------------------------------------- parsing
def test_parse_structured_answer_and_claims():
    p = parse_structured(ANSWER)
    assert p["recommendation"] == "BUY" and p["probability_up"] == 68 and p["confidence"] == 82
    assert p["entry"] == 1515.0 and p["target"] == 1560 and p["stop_loss"] == 1480
    assert p["risks"] == ["RSI elevated", "market volatility"]
    keys = [c["key"] for c in p["claims"]]
    assert keys == ["breakout", "volume_high", "positive_news"] and p["claims"][2]["ref"] == "N1"


def test_parse_structured_rejects_incomplete_and_clamps():
    assert parse_structured("I think it's a buy") is None
    assert parse_structured("RECOMMENDATION: BUY") is None
    p = parse_structured("RECOMMENDATION: sell\nPROBABILITY_UP: 0.05\nTARGET: NONE")
    assert p["recommendation"] == "SELL" and p["probability_up"] == 10 and p["target"] is None
    assert p["direction"] == "down"


def test_parse_structured_multiline_reasoning():
    p = parse_structured("RECOMMENDATION: HOLD\nPROBABILITY_UP: 50\nREASONING:\nFirst line.\nSecond line.\n"
                         "CLAIM: uptrend | trend up | 60\nstray text")
    assert p["reasoning"] == "First line. Second line." and len(p["claims"]) == 1


def test_parse_claims_handles_noise():
    claims = parse_claims("**CLAIM:** Volume High | big volume | 0.9\nCLAIM 2: negative_news | see N3 | 40")
    assert claims[0].key == "volume_high" and claims[0].confidence == 90
    assert claims[1].ref == "N3"


# ----------------------------------------------------------------------- evidence
def test_verify_supported_contradicted_unknown_and_fabricated():
    claims = [Claim("breakout", "above resistance", 90), Claim("volume_low", "weak volume", 80),
              Claim("fii_buying", "FIIs buying", 50), Claim("positive_news", "news", 70, ref="N9"),
              Claim("positive_news", "order win", 70, ref="N1"), Claim("moon_phase", "full moon", 99)]
    r = verify(claims, FACTS, HEADLINES, "BUY")
    status = {(c.key, c.model_evidence): c.status for c in r.checks}
    assert status[("breakout", "above resistance")] == SUPPORTED
    assert status[("volume_low", "weak volume")] == NOT_SUPPORTED
    assert status[("fii_buying", "FIIs buying")] == UNKNOWN          # data missing -> never guessed
    assert status[("positive_news", "news")] == NOT_SUPPORTED        # N9 does not exist
    assert status[("positive_news", "order win")] == SUPPORTED
    assert status[("moon_phase", "full moon")] == UNVERIFIABLE
    assert r.fabricated_news == 1 and r.supported == 2 and r.not_supported == 2
    assert 0 < r.evidence_score < 1
    news = next(c for c in r.checks if c.news)
    assert news.news["exists"] and news.news["source"] == "ET"


def test_verify_flags_news_mentioned_without_any_headline():
    r = verify([], {**FACTS, "stock_news_count": 0}, [], "BUY", news_text="Strong results announced yesterday")
    assert r.fabricated_news == 1 and any("no headline" in f for f in r.flags)
    ok = verify([], FACTS, [], "BUY", news_text="No material news")
    assert ok.fabricated_news == 0


def test_interpretation_and_predictions_are_not_facts():
    r = verify([Claim("breakout", "x", 90)], FACTS, HEADLINES, "BUY",
               predictions={"target": 1560}, interpretation="Momentum is strong")
    cats = {i["category"] for i in r.items}
    assert {"FACT", "PREDICTION", "MODEL_INTERPRETATION"} <= cats


def test_present_factors_and_levels():
    f = present_factors(FACTS)
    assert "breakout" in f and "volume_high" in f and "rsi_overbought" in f and "breakdown" not in f
    assert check_levels("BUY", 1515, 30, 1515, 1500, 1480)          # target below price
    assert check_levels("BUY", 1515, 30, 1515, 1560, 1480) == []


# ----------------------------------------------------------------------- consensus
def member(model, rec, prob, checks, score, fabricated=0, stats=None):
    return {"model": model, "profile_id": hash(model) % 100, "recommendation": rec, "probability_up": prob,
            "confidence": 90, "entry": 1515, "target": 1560 if rec == "BUY" else None,
            "stop_loss": 1480 if rec == "BUY" else None, "reasoning": f"{model} reasoning",
            "evidence": {"evidence_score": score, "supported": 2, "not_supported": 0,
                         "fabricated_news": fabricated, "checks": checks},
            "reliability_stats": stats or {}}


def chk(key, status, bias="bullish"):
    return {"key": key, "label": key, "actual": "…", "bias": bias, "status": status}


def test_evidence_outweighs_vote_count():
    weak_buy = [chk("volume_high", "NOT_SUPPORTED"), chk("positive_news", "NOT_SUPPORTED")]
    strong_sell = [chk("rsi_overbought", "SUPPORTED", "bearish"), chk("breakdown", "SUPPORTED", "bearish")]
    members = [member("qwen", "BUY", 70, weak_buy, 0.0, fabricated=1),
               member("claude", "BUY", 68, weak_buy, 0.0, fabricated=1),
               member("kimi", "SELL", 25, strong_sell, 1.0)]
    r = combine(members, [], FACTS)
    assert r.vote_text == "2 BUY / 1 SELL"
    assert r.signal != "BUY"                         # the vote alone would have said BUY
    assert any(d["model"] == "kimi" for d in r.disagreement) or r.signal == "SELL"
    assert r.members[2]["weight"] > r.members[0]["weight"]


def test_disagreement_is_preserved_and_confidence_is_not_evidence():
    good = [chk("breakout", "SUPPORTED"), chk("volume_high", "SUPPORTED")]
    proven = {"n": 40, "hits": 22}
    members = [member("qwen", "BUY", 70, good, 1.0, stats=proven), member("claude", "BUY", 72, good, 1.0, stats=proven),
               member("kimi", "SELL", 35, [chk("rsi_overbought", "SUPPORTED", "bearish")], 1.0, stats=proven)]
    r = combine(members, [], FACTS)
    assert r.signal == "BUY" and r.votes == {"BUY": 2, "HOLD": 0, "SELL": 1}
    assert r.disagreement[0]["model"] == "kimi" and "rsi_overbought" in r.disagreement[0]["supported"]
    assert any(x["key"] == "rsi_overbought" for x in r.risks)
    assert {e["key"] for e in r.evidence_for} == {"breakout", "volume_high"}
    # stated confidence of 90 for everyone is not what the weights are made of
    assert all("confidence" not in m["evidence_why"] for m in r.members)


def test_true_facts_that_argue_against_the_call_do_not_support_it():
    # SELL backed only by a bullish (true) fact vs the same SELL backed by bearish facts
    against = combine([member("qwen", "SELL", 20, [chk("positive_news", "SUPPORTED", "bullish")], 1.0),
                       member("claude", "HOLD", 50, [chk("uptrend", "SUPPORTED")], 1.0)], [], FACTS)
    backed = combine([member("qwen", "SELL", 20, [chk("breakdown", "SUPPORTED", "bearish")], 1.0),
                      member("claude", "HOLD", 50, [chk("uptrend", "SUPPORTED")], 1.0)], [], FACTS)
    assert against.members[0]["weight"] < backed.members[0]["weight"]
    assert "0 of 1 supported claims" in against.members[0]["evidence_why"]


def test_unproven_extreme_probabilities_are_discounted():
    from consensus_engine import calibrated_probability
    assert calibrated_probability(10, {})[0] == 30.0            # halfway to 50 with no record
    assert calibrated_probability(10, {"n": 30})[0] == 10.0     # face value once proven
    r = combine([member("qwen", "SELL", 10, [chk("breakdown", "SUPPORTED", "bearish")], 1.0),
                 member("claude", "HOLD", 50, [chk("uptrend", "SUPPORTED")], 1.0)], [], FACTS)
    assert r.scores["model_probability"] >= 40                  # one unproven 10% can't force a SELL


def test_weak_evidence_holds_back_signal_and_empty_is_none():
    r = combine([member("qwen", "BUY", 80, [chk("volume_high", "NOT_SUPPORTED")], 0.1)], [], FACTS)
    assert r.signal == "HOLD" and "Held back" in r.reasoning[1]
    assert combine([], [], FACTS) is None
    assert bucket("AVOID") == "SELL" and bucket("maybe") == "HOLD"


def test_reliability_uses_context_and_shrinks():
    assert reliability({})[0] == 1.0
    lucky = reliability({"n": 3, "hits": 3})[0]
    proven = reliability({"n": 200, "hits": 150})[0]
    assert 1.0 < lucky < proven
    ctx = reliability({"n": 200, "hits": 150, "context_n": 40, "context_hits": 12, "context_label": "Breakout setups"})
    assert ctx[0] < 1.0 and "Breakout" in ctx[1]


def test_historical_probability_needs_samples():
    assert historical_probability([{"pattern": "breakout", "bias": "bullish", "occurrences": 5, "successes": 5,
                                    "base_up_rate": 50}])[0] is None
    p, lines = historical_probability([{"pattern": "breakout", "bias": "bullish", "occurrences": 500,
                                        "successes": 360, "base_up_rate": 52.0, "source": "history"}])
    assert p == 72.0 and lines[0]["edge"] == 20.0


# ----------------------------------------------------------------------- research package
def test_research_package_is_timestamped_hashed_and_marks_unknown():
    tech = SimpleNamespace(close=100.0, prev_close=99.0, change_pct=1.0, ema_20=98.0, ema_50=None, ema_200=None,
                           sma_20=97.0, sma_50=None, vwap_20=None, rsi=60.0, macd=1.0, macd_signal=0.5, macd_hist=0.5,
                           atr=2.0, atr_pct=2.0, support=95.0, resistance=101.0, high_52w=None, low_52w=None,
                           return_20d=3.0, return_60d=None, relative_strength_20d=1.0, volume_ratio=1.4,
                           breakout=False, breakdown=False, trend="uptrend", setups=["Trend Momentum"],
                           technical_score=60.0, last_bar_time="2026-09-29T15:30:00+05:30", backtest={})
    item = SimpleNamespace(technical=tech, plan=None, sector="IT", symbol="ACME", name="Acme", strategy="Trend Momentum",
                           conviction_score=61.0, sentiment={"headlines": [], "ai_reads": []})
    news = [{"title": "Fed cuts rates", "market_moving": True, "source": "ET", "published": "2026-09-29", "sentiment": 1}]
    a = build_package(item, {"regime": "neutral", "regimes": ["BULLISH"]}, news, "Yahoo")
    b = build_package(item, {"regime": "neutral", "regimes": ["BULLISH"]}, news, "Yahoo",
                      now=pd.Timestamp("2030-01-01").to_pydatetime())
    assert a.content_hash() == b.content_hash() and a.collected_at != b.collected_at
    text = render(a)
    assert R_UNKNOWN in text and "[N1]" in text and "market-wide" in text
    assert a.facts["ema_50"] is None and a.facts["stock_news_count"] == 0


def test_regimes_and_market_moving_news():
    assert classify_regimes("uptrend", 22.0, -1.5, None, 3) == ["BULLISH", "HIGH_VOLATILITY", "NEWS_DRIVEN"]
    assert classify_regimes("sideways", None, 0.1, 0.5, 0) == ["SIDEWAYS", "LOW_VOLATILITY"]
    assert is_market_moving("RBI keeps repo rate unchanged") and not is_market_moving("Acme opens new store")


# ----------------------------------------------------------------------- credentials
class MemoryKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, s, n, p):
        self.store[(s, n)] = p

    def get_password(self, s, n):
        return self.store.get((s, n))

    def delete_password(self, s, n):
        self.store.pop((s, n))


def test_credentials_go_to_vault_and_resolve(monkeypatch):
    kr = MemoryKeyring()
    monkeypatch.setattr(credential_store, "_keyring", lambda: kr)
    ref = credential_store.store_secret("llm-profile-7", "sk-secret-123456789")
    assert ref == "keyring:llm-profile-7" and "sk-secret" not in ref
    assert credential_store.read_secret(ref) == "sk-secret-123456789"
    assert "sk-" not in mask_key(ref)
    assert credential_store.store_secret("x", "not-needed") == "not-needed"
    credential_store.delete_secret(ref)
    assert credential_store.read_secret(ref) is None


# ----------------------------------------------------------------------- provider isolation
class FakeProvider:
    def __init__(self, behaviour):
        self.behaviour, self.calls = list(behaviour), 0

    async def chat_ex(self, messages, **kw):
        self.calls += 1
        step = self.behaviour.pop(0)
        if isinstance(step, Exception):
            raise step
        if step == "hang":
            await asyncio.Event().wait()   # never answers
        return ChatResult(step, 100, 50)


def snap(pid, local=False, timeout=1):
    return ProfileSnapshot(id=pid, name=f"m{pid}", kind="openai_compatible", model=f"model-{pid}", base_url=None,
                           api_key_ref=None, is_local=local, temperature=None, max_tokens=None, timeout_s=timeout,
                           daily_limit=0, hourly_limit=0, updated_at=pd.Timestamp("2026-01-01").to_pydatetime())


@pytest.fixture
def orch(monkeypatch):
    o = AIOrchestrator()
    monkeypatch.setattr(AIOrchestrator, "_reserve", staticmethod(lambda p: None))
    monkeypatch.setattr(AIOrchestrator, "record_usage", staticmethod(lambda *a, **k: None))
    real_sleep = asyncio.sleep
    monkeypatch.setattr("ai_orchestrator.asyncio.sleep", lambda s: real_sleep(0))
    return o


def run_with(o, providers, profiles):
    o._provider = lambda p: providers[p.id]

    async def go():
        return await asyncio.gather(*(o.call_model(p, "prompt") for p in profiles))
    return asyncio.run(go())


def test_failures_are_isolated_and_retried_only_when_transient(orch):
    providers = {
        1: FakeProvider([ANSWER]),                                                       # works
        2: FakeProvider([ProviderError("Invalid API key for this provider.")]),          # no retry
        3: FakeProvider([ProviderError("Rate limited by the provider — try again shortly.", retryable=True)]),
        4: FakeProvider([ProviderError("Can't reach the provider", retryable=True), ANSWER]),  # retried once
    }
    results = run_with(orch, providers, [snap(1), snap(2), snap(3), snap(4)])
    assert results[0]["ok"] and results[0]["input_tokens"] == 100
    assert not results[1]["ok"] and "Invalid API key" in results[1]["error"] and providers[2].calls == 1
    assert not results[2]["ok"] and providers[3].calls == 1
    assert results[3]["ok"] and providers[4].calls == 2


def test_timeout_is_isolated(orch, monkeypatch):
    providers = {1: FakeProvider(["hang"]), 2: FakeProvider([ANSWER])}
    p1 = snap(1, timeout=1)
    p1.timeout_s = 1
    import ai_orchestrator as mod
    original = mod.asyncio.wait_for

    async def fast_wait_for(coro, timeout):
        return await original(coro, timeout=0.2)

    monkeypatch.setattr(mod.asyncio, "wait_for", fast_wait_for)
    results = run_with(orch, providers, [p1, snap(2)])
    assert not results[0]["ok"] and "timed out" in results[0]["error"]
    assert results[1]["ok"]


def test_cap_reached_skips_call(monkeypatch):
    o = AIOrchestrator()
    monkeypatch.setattr(AIOrchestrator, "_reserve", staticmethod(lambda p: "hourly cap of 5 requests reached"))
    provider = FakeProvider([ANSWER])
    o._provider = lambda p: provider
    r = asyncio.run(o.call_model(snap(1), "prompt"))
    assert not r["ok"] and "hourly cap" in r["error"] and provider.calls == 0


# ----------------------------------------------------------------------- outcomes and learning
def bars(rows):
    return pd.DataFrame(rows, columns=["high", "low", "close"])


def test_trade_outcome_from_real_bars():
    b = bars([(102, 99, 101), (106, 100, 105), (104, 97, 98)])
    o = trade_outcome(100.0, b, target=105, stop=95, recommendation="BUY")
    assert o["target_hit"] is True and o["stop_hit"] is False and o["pnl_pct"] == 5.0
    assert o["max_favorable_pct"] == 6.0 and o["max_adverse_pct"] == -3.0
    stopped = trade_outcome(100.0, bars([(101, 94, 96), (110, 95, 108)]), target=105, stop=95, recommendation="BUY")
    assert stopped["stop_hit"] and stopped["pnl_pct"] == -5.0
    assert trade_outcome(100.0, b, None, None, "HOLD")["pnl_pct"] is None
    assert trade_outcome(100.0, b, None, None, "SELL")["pnl_pct"] == 2.0


def test_pattern_counters_count_real_outcomes():
    from collections import defaultdict
    counters = defaultdict(_Counter)
    accumulate(counters, ["breakout", "volume_high"], "BULLISH", 2.0, ["claude"])
    accumulate(counters, ["breakout"], "BULLISH", -1.0)
    b = counters[("breakout", "bullish", "ALL")]
    assert (b.n, b.ok) == (2, 1)
    assert counters[("breakout+volume_high", "bullish", "ALL")].n == 1
    assert counters[("ALL", "bullish", "BULLISH")].n == 2
    assert "claude" in b.models
