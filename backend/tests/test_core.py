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
    BREAKOUT, NO_SETUP, AnalysisService, session_fraction,
)
from kite_service import KiteService  # noqa: E402
from learning_service import grade_against_candles, parse_holding_days  # noqa: E402
from llm_service import clean_output  # noqa: E402
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
