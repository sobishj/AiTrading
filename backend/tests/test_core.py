"""
Unit tests for the deterministic core (no network, no database):
    cd backend && venv\\Scripts\\python -m pytest -q
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis_service import (  # noqa: E402
    BREAKOUT, NO_SETUP, AnalysisService, projected_volume_share, session_fraction, timing_score,
)
from data_provider import clean_daily_candles  # noqa: E402
from kite_service import KiteService  # noqa: E402
from learning_service import grade_against_candles, parse_holding_days  # noqa: E402
from llm_service import clean_output, ungrounded_numbers  # noqa: E402
from market_service import headline_sentiment, keyword_pattern  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
svc = AnalysisService()


def make_candles(closes, volumes=None, start="2025-01-01", spread=0.01):
    closes = np.asarray(closes, dtype=float)
    dates = pd.bdate_range(start=start, periods=len(closes), tz="Asia/Kolkata") + pd.Timedelta(hours=9, minutes=15)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    volumes = np.full(len(closes), 100_000) if volumes is None else np.asarray(volumes)
    return pd.DataFrame({"date": dates, "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes})


FAR_FUTURE = datetime(2030, 1, 1, tzinfo=IST)  # so no bar is treated as today's partial session


# ---------------------------------------------------------------- analysis
def test_uptrend_scores_higher_than_downtrend():
    rng = np.random.default_rng(1)
    up = 100 * np.cumprod(1 + 0.004 + rng.normal(0, 0.008, 260))
    down = 100 * np.cumprod(1 - 0.004 + rng.normal(0, 0.008, 260))
    s_up = svc.compute_indicators("UP", make_candles(up), now=FAR_FUTURE)
    s_down = svc.compute_indicators("DOWN", make_candles(down), now=FAR_FUTURE)
    assert s_up.trend == "uptrend"
    assert s_down.trend == "downtrend"
    assert s_up.technical_score > s_down.technical_score + 30


def test_breakout_detected_on_high_volume_new_high():
    closes = list(np.linspace(100, 110, 240)) + [110] * 19 + [118]
    volumes = [100_000] * 259 + [300_000]
    snap = svc.compute_indicators("BRK", make_candles(closes, volumes, spread=0.002), now=FAR_FUTURE)
    assert BREAKOUT in snap.setups
    assert snap.volume_spike


def test_trade_plan_invariants():
    rng = np.random.default_rng(7)
    closes = 200 * np.cumprod(1 + 0.003 + rng.normal(0, 0.01, 300))
    snap = svc.compute_indicators("PLAN", make_candles(closes), now=FAR_FUTURE)
    for strategy in (BREAKOUT, NO_SETUP, "Pullback to EMA", "Trend Momentum"):
        plan = svc.build_trade_plan(snap, strategy, conviction=70)
        assert plan.stop_loss < plan.entry_low <= plan.entry_high < plan.target
        assert plan.risk_reward > 1.4
        # Stop never further than 1.5 ATR (+ tick rounding) from the entry midpoint.
        assert plan.entry_mid - plan.stop_loss <= 1.5 * snap.atr + 0.1


def test_downtrend_plan_is_avoid():
    closes = np.linspace(300, 200, 260)
    snap = svc.compute_indicators("DN", make_candles(closes), now=FAR_FUTURE)
    assert svc.build_trade_plan(snap, NO_SETUP, conviction=30).action == "AVOID"


def test_too_little_history_returns_empty_snapshot():
    snap = svc.compute_indicators("NEW", make_candles([100] * 10))
    assert not snap.has_data
    assert svc.build_trade_plan(snap, NO_SETUP, 50) is None


def test_session_fraction_scales_only_todays_bar():
    now = datetime(2026, 9, 24, 12, 22, tzinfo=IST)  # 187 of 375 minutes elapsed
    today_bar = pd.Timestamp("2026-09-24 09:15", tz="Asia/Kolkata")
    yesterday_bar = pd.Timestamp("2026-09-23 09:15", tz="Asia/Kolkata")
    assert session_fraction(today_bar, now) == pytest.approx(187 / 375)
    assert session_fraction(yesterday_bar, now) == 1.0


def test_early_session_volume_is_not_overprojected():
    # 9:20 IST is ~1% of the session but already ~10% of a typical day's volume.
    assert projected_volume_share(5 / 375) == pytest.approx(0.10)
    assert projected_volume_share(60 / 375) > 60 / 375
    assert projected_volume_share(1.0) == 1.0
    shares = [projected_volume_share(f) for f in np.linspace(0.05, 1.0, 20)]
    assert shares == sorted(shares)


def test_snapshot_at_last_bar_matches_live_snapshot():
    rng = np.random.default_rng(11)
    closes = 150 * np.cumprod(1 + 0.002 + rng.normal(0, 0.01, 300))
    candles = make_candles(closes)
    live = svc.compute_indicators("SAME", candles, now=FAR_FUTURE)
    df = svc.indicator_frame(candles)
    replayed = svc.snapshot_at("SAME", df, svc.setup_flags(df), len(df) - 1)
    assert replayed.technical_score == live.technical_score
    assert replayed.setups == live.setups


def test_timing_prefers_dip_in_uptrend_over_short_term_spike():
    rise = list(100 * np.cumprod(np.full(275, 1.003)))   # ~year-long steady uptrend
    dip = svc.compute_indicators("DIP", make_candles(rise + [rise[-1] * f for f in (0.98, 0.965, 0.955, 0.95, 0.945)]),
                                 now=FAR_FUTURE)
    spike = svc.compute_indicators("SPIKE", make_candles(rise + [rise[-1] * f for f in (1.03, 1.06, 1.09, 1.12, 1.15)]),
                                   now=FAR_FUTURE)
    # Same long-term trend, so the gap is purely short-term timing.
    assert timing_score(dip) > 2
    assert timing_score(spike) < 0
    assert timing_score(dip) - timing_score(spike) > 5


def test_market_below_ema50_blocks_new_buys():
    rng = np.random.default_rng(7)
    closes = 200 * np.cumprod(1 + 0.003 + rng.normal(0, 0.01, 300))
    snap = svc.compute_indicators("GATE", make_candles(closes), now=FAR_FUTURE)
    snap.technical_score = 70
    assert svc.build_trade_plan(snap, "Trend Momentum", conviction=75).action == "BUY"
    assert svc.build_trade_plan(snap, "Trend Momentum", conviction=75, market_uptrend=False).action == "WAIT"


def test_plan_reasons_match_the_decision():
    rng = np.random.default_rng(7)
    closes = 200 * np.cumprod(1 + 0.003 + rng.normal(0, 0.01, 300))
    snap = svc.compute_indicators("WHY", make_candles(closes), now=FAR_FUTURE)
    snap.technical_score = 70
    assert svc.build_trade_plan(snap, "Trend Momentum", conviction=75).reasons == []
    no_setup = svc.build_trade_plan(snap, NO_SETUP, conviction=75)
    assert no_setup.action == "WAIT" and no_setup.reasons == ["no entry setup has triggered yet"]
    low = svc.build_trade_plan(snap, "Trend Momentum", conviction=50, market_uptrend=False)
    assert low.action == "WAIT"
    assert any("conviction 50/100" in r for r in low.reasons) and any("NIFTY" in r for r in low.reasons)
    snap.technical_score = 30
    avoid = svc.build_trade_plan(snap, "Trend Momentum", conviction=75)
    assert avoid.action == "AVOID" and any("technical score 30/100" in r for r in avoid.reasons)


def test_backtest_skips_live_bar_and_counts():
    rng = np.random.default_rng(3)
    closes = 100 * np.cumprod(1 + 0.002 + rng.normal(0, 0.012, 300))
    snap = svc.compute_indicators("BT", make_candles(closes), now=FAR_FUTURE)
    for stats in snap.backtest.values():
        assert stats.wins + stats.losses == stats.signals
        if stats.signals:
            assert 0 <= stats.win_rate <= 100


# ---------------------------------------------------------------- learning
def _rec(entry_low, entry_high, stop, target, days_ago=20, holding="3-5 trading days"):
    return SimpleNamespace(
        entry_low=entry_low, entry_high=entry_high, entry_price=(entry_low + entry_high) / 2,
        stop_loss=stop, target_price=target, holding_period=holding,
        timestamp=datetime(2025, 1, 1, 4, 0),  # naive UTC, before the first candle below
    )


def _bars(rows):
    dates = pd.bdate_range("2025-01-02", periods=len(rows), tz="Asia/Kolkata")
    return pd.DataFrame([{"date": d, "open": o, "high": h, "low": low, "close": c}
                         for d, (o, h, low, c) in zip(dates, rows)])


def test_grading_target_hit_after_fill():
    rec = _rec(99, 101, 95, 110)
    bars = _bars([(100, 102, 99, 101), (101, 111, 100, 110), (110, 112, 108, 111)])
    assert grade_against_candles(rec, bars)["outcome"] == "target_hit"


def test_grading_stop_on_fill_bar_is_conservative():
    rec = _rec(99, 101, 95, 110)
    bars = _bars([(100, 111, 94, 100)] + [(100, 101, 99, 100)] * 4)
    assert grade_against_candles(rec, bars)["outcome"] == "stop_hit"


def test_grading_not_triggered_and_pending():
    rec = _rec(99, 101, 95, 110)
    gap_up = [(105, 108, 104, 106)] * 5
    assert grade_against_candles(rec, _bars(gap_up))["outcome"] == "not_triggered"
    assert grade_against_candles(rec, _bars(gap_up[:2])) is None  # window still open


def test_parse_holding_days_uses_upper_bound():
    assert parse_holding_days("3-10 trading days") == 10
    assert parse_holding_days("1-3 weeks") == 15
    assert parse_holding_days("") == 10


# ---------------------------------------------------------------- tradebook parsing
def test_parse_trade_report_finds_header_below_metadata():
    csv = (
        "Client ID,AB1234\n"
        "Tradebook for Equity,\n"
        "\n"
        "Symbol,ISIN,Trade Date,Exchange,Segment,Series,Trade Type,Auction,Quantity,Price,Trade ID\n"
        "BEL,INE263A01024,2026-09-01,NSE,EQ,EQ,buy,false,\"1,000\",400.5,111\n"
    ).encode()
    df = KiteService.parse_trade_report(csv, "tradebook.csv")
    assert list(df["symbol"]) == ["BEL"]
    assert df["quantity"].iloc[0] == 1000
    assert df["price"].iloc[0] == 400.5


# ---------------------------------------------------------------- news + llm
def test_keyword_pattern_short_caps_are_case_sensitive():
    pattern = keyword_pattern(["BEL", "Bharat Electronics"])
    assert pattern.search("BEL bags defence order")
    assert pattern.search("bharat electronics shares rise")
    assert not pattern.search("Sensex closes below 80,000")  # 'bel' inside 'below'
    assert not pattern.search("bel air")


def test_headline_sentiment_uses_whole_words():
    assert headline_sentiment("Company posts record profit, shares surge") == 1
    assert headline_sentiment("Stock falls after weak results") == -1
    assert headline_sentiment("Board meeting update scheduled") == 0  # 'up' in 'update' must not count


def test_clean_output_strips_think_blocks():
    assert clean_output("<think>internal</think>\nAnswer") == "Answer"
    assert clean_output("Answer<think>unfinished") == "Answer"


def test_short_keyword_skips_group_companies():
    pattern = keyword_pattern(["SBI", "State Bank of India"])
    assert pattern.search("SBI raises lending rates")
    assert not pattern.search("SBI Life, HDFC Life tank on IRDAI paper")
    assert not pattern.search("SBI Card spends rise")


def test_llm_numbers_must_come_from_the_facts():
    facts = "Entry 1777.40-1802.50, stop 1727.20, target 1915.45, conviction 69.1/100, 3-10 trading days"
    assert ungrounded_numbers("Buy near 1,802 with a stop at 1727.2; target ₹1,915 (conviction 69).", facts) == []
    assert ungrounded_numbers("Hold 3-10 days, 2 reasons.", facts) == []
    assert ungrounded_numbers("Target 1,950 and stop 1,700.", facts) == ["1,950", "1,700"]


def test_clean_daily_candles_drops_placeholders_and_adjusts_splits():
    closes = [300.0, 303, 306, 309, 206, 208]            # 3:2 bonus between bar 3 and 4
    df = make_candles(closes, volumes=[1000] * 6, spread=0.0)
    df.loc[4, "open"] = 206.0
    placeholder = df.iloc[[2]].assign(date=df["date"].iloc[2] + pd.Timedelta(hours=1), volume=0,
                                      open=306.0, high=306.0, low=306.0, close=306.0)
    df = pd.concat([df, placeholder]).sort_values("date").reset_index(drop=True)
    out = clean_daily_candles(df, "TEST")
    assert len(out) == 6 and (out["volume"] > 0).all()
    factor = 206 / 309
    assert out["close"].iloc[3] == pytest.approx(309 * factor)
    assert out["close"].iloc[0] == pytest.approx(300 * factor)
    assert out["close"].iloc[4] == 206                   # post-event prices untouched
    assert out["volume"].iloc[0] == round(1000 / factor)


def test_clean_daily_candles_keeps_real_crash_days():
    df = make_candles([100.0, 100, 83, 85], spread=0.0)   # -17% gap: largest real one since 2019
    df.loc[2, "open"] = 83.0
    assert clean_daily_candles(df)["close"].tolist() == [100.0, 100, 83, 85]


def test_list_orders_buy_then_wait_then_avoid():
    from ranking_service import RankingService

    def item(symbol, action, conviction):
        return SimpleNamespace(symbol=symbol, action=action, conviction_score=conviction, strategy="Pullback to EMA",
                               technical=SimpleNamespace(technical_score=60.0, volume_ratio=1.0),
                               sentiment={"score": 50.0, "sentiment": "neutral"},
                               rank=0, previous_rank=None, change_reason=None)

    svc_rank = RankingService()
    ranked = svc_rank.rerank_watchlist([item("AV", "AVOID", 80), item("W1", "WAIT", 60), item("B1", "BUY", 56),
                                        item("W2", "WAIT", 70)])
    assert [r.symbol for r in ranked] == ["B1", "W2", "W1", "AV"]
    assert [r.rank for r in ranked] == [1, 2, 3, 4]
    # A later run where the BUY turns WAIT explains the move by the action change.
    again = svc_rank.rerank_watchlist([item("B1", "WAIT", 56), item("W2", "WAIT", 70)])
    moved = next(r for r in again if r.symbol == "B1")
    assert moved.rank == 2 and "now WAIT (was BUY)" in moved.change_reason


# ---------------------------------------------------------------- model credit
def test_out_of_credit_errors_are_recognised():
    from llm_providers import _openai_error

    class Fake(Exception):
        def __init__(self, status_code, text, code=None):
            super().__init__(text)
            self.status_code, self.code = status_code, code

    assert _openai_error(Fake(402, "Insufficient Balance")).no_credit                      # DeepSeek / OpenRouter
    assert _openai_error(Fake(429, "You exceeded your current quota", "insufficient_quota")).no_credit   # OpenAI
    assert not _openai_error(Fake(429, "Rate limit reached for requests")).no_credit


@pytest.mark.parametrize("base_url,body,expected", [
    ("https://api.deepseek.com/v1", {"is_available": True, "balance_infos": [
        {"currency": "USD", "total_balance": "12.40"}]}, {"amount": 12.4, "currency": "USD"}),
    ("https://openrouter.ai/api/v1", {"data": {"total_credits": 20, "total_usage": 7.5}},
     {"amount": 12.5, "currency": "USD"}),
    ("https://api.moonshot.ai/v1", {"data": {"available_balance": 3.2}}, {"amount": 3.2, "currency": "USD"}),
])
def test_fetch_balance_parses_each_provider(monkeypatch, base_url, body, expected):
    import asyncio
    import httpx
    import llm_providers
    from llm_providers import ProviderConfig, fetch_balance

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(llm_providers.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    config = ProviderConfig(kind="openai_compatible", model="m", base_url=base_url, api_key="k")
    assert asyncio.run(fetch_balance(config)) == expected
    assert seen["url"].split("/")[2] == base_url.split("/")[2]


def test_providers_without_balance_api_return_none():
    import asyncio
    from llm_providers import ProviderConfig, fetch_balance
    for config in (ProviderConfig(kind="anthropic", model="claude-opus-5-5"),
                   ProviderConfig(kind="openai_compatible", model="m", base_url="https://api.openai.com/v1")):
        assert asyncio.run(fetch_balance(config)) is None


def test_list_price_matches_dated_model_ids_only():
    from llm_providers import list_price
    assert list_price("claude-opus-5-5") == (4.0, 20.0)
    assert list_price("claude-haiku-4-5-20251001") == (1.0, 5.0)
    assert list_price("gpt-unknown") is None   # never guess a price


def test_provider_request_limits_are_not_reported_as_out_of_credit():
    import httpx
    import openai
    from llm_providers import _openai_error

    def err(details, message="You exceeded your current quota, please check your plan and billing details."):
        body = {"error": {"code": 429, "message": message, "details": details}}
        return openai.RateLimitError(str([body]), response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
                                     body=body)

    daily = _openai_error(err([{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"},
                               {"retryDelay": "19s"}]))
    assert not daily.no_credit and not daily.retryable and "Daily free-tier request limit reached (20" in daily.message
    minute = _openai_error(err([{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]))
    assert minute.retryable and not minute.no_credit
    assert _openai_error(err([], message="insufficient_quota: You exceeded your current quota")).no_credit


def test_chat_day_bounds_are_ist_midnight_in_utc():
    from datetime import date as d
    from insight_service import ist_day_bounds
    start, end = ist_day_bounds(d(2026, 10, 1))
    # 1 Oct 00:00 IST = 30 Sep 18:30 UTC; a chat at 23:59 IST on 1 Oct (18:29 UTC on 1 Oct) is still that day.
    assert start == datetime(2026, 9, 30, 18, 30) and end == datetime(2026, 10, 1, 18, 30)
    assert start <= datetime(2026, 10, 1, 18, 29) < end
