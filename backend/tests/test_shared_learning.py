"""Shared learning across models and grading accuracy — no DB, no network."""
import os
import sys
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_analyst_service import STANDARD_THRESHOLDS, grounded_lessons  # noqa: E402
from data_provider import clean_daily_candles, completed_daily_bars, reference_price  # noqa: E402
from learning_service import grade_against_candles  # noqa: E402
from prompts.prompt_library import PromptLibrary  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def candles(closes, start="2026-09-21", opens=None):
    closes = np.asarray(closes, dtype=float)
    dates = pd.bdate_range(start=start, periods=len(closes), tz=IST) + pd.Timedelta(hours=9, minutes=15)
    opens = np.concatenate([[closes[0]], closes[:-1]]) if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({"date": dates, "open": opens, "high": np.maximum(opens, closes) * 1.01,
                         "low": np.minimum(opens, closes) * 0.99, "close": closes, "volume": 100_000})


# ---------------------------------------------------------------- unfinished bars
def test_todays_bar_is_dropped_until_the_session_has_closed():
    df = candles([100, 101, 102, 103, 104])               # last bar: Fri 25 Sep 2026
    friday = df["date"].iloc[-1].date()
    assert len(completed_daily_bars(df, now=datetime.combine(friday, datetime.min.time(), IST)
                                    .replace(hour=11))) == 4
    assert len(completed_daily_bars(df, now=datetime.combine(friday, datetime.min.time(), IST)
                                    .replace(hour=16, minute=5))) == 5
    assert len(completed_daily_bars(df, now=datetime(2026, 9, 28, 10, 0, tzinfo=IST))) == 5   # next Monday


# ---------------------------------------------------------------- corporate actions
def test_split_after_a_forecast_is_not_graded_as_a_crash():
    # 1:2 split on the 4th bar; Yahoo left it unadjusted, so clean_daily_candles back-adjusts.
    raw = candles([200, 202, 204, 103, 104, 106], opens=[200, 200, 202, 103, 103, 104])
    df = clean_daily_candles(raw, "TEST")
    split_day = df["date"].iloc[3].date()
    forecast_day = df["date"].iloc[1].date()
    price, scale = reference_price(df, forecast_day, 202.0)   # price stored before the split
    assert scale == pytest.approx(103 / 204)
    assert price == pytest.approx(df["close"].iloc[1])
    # A price recorded on/after the split needs no change.
    assert reference_price(df, split_day, 103.0) == (103.0, 1.0)


def test_feed_side_adjustment_falls_back_to_the_series_close():
    df = candles([101, 102, 103, 104])                     # already split-adjusted by the feed
    day = df["date"].iloc[1].date()
    price, scale = reference_price(df, day, 204.0)         # stored pre-split raw price
    assert price == 102.0 and scale == pytest.approx(0.5)


def test_recommendation_replay_survives_a_split():
    raw = candles([200, 202, 204, 103, 104, 112, 113], opens=[200, 200, 202, 103, 103, 104, 112])
    df = clean_daily_candles(raw, "TEST")
    issued = df["date"].iloc[0].tz_convert(IST).replace(hour=10).tz_convert("UTC").tz_localize(None).to_pydatetime()
    rec = SimpleNamespace(entry_low=198.0, entry_high=203.0, entry_price=200.0, stop_loss=190.0,
                          target_price=220.0, holding_period="5 trading days", timestamp=issued)
    result = grade_against_candles(rec, df)
    # Unadjusted, the 103 bar would read as a stop-out at 190; in fact the target (110 post-split) traded.
    assert result["outcome"] == "target_hit"
    assert result["exit_price"] == pytest.approx(220.0, rel=0.01)   # reported on the recommendation's scale


# ---------------------------------------------------------------- shared lessons
def test_lessons_quoting_invented_numbers_are_dropped():
    record = "- [qwen] Acme: predicted up (62% up) → actual -1.4% (WRONG)\n12 forecasts graded, 58% correct"
    kept, dropped = grounded_lessons([
        "Breakouts with RSI above 70 failed more often than they worked.",
        "Forecasts were right 58% of the time, so keep probabilities modest.",
        "Bullish calls succeed 83% of the time after a 4.5% gap.",
    ], record + "\n" + STANDARD_THRESHOLDS)
    assert kept == ["Breakouts with RSI above 70 failed more often than they worked.",
                    "Forecasts were right 58% of the time, so keep probabilities modest."]
    assert dropped == ["Bullish calls succeed 83% of the time after a 4.5% gap."]


def test_every_model_gets_the_shared_lessons_in_its_prompt():
    prompt = PromptLibrary.structured_analysis(
        name="Acme", symbol="ACME", horizon=5, collected_at="2026-10-01T09:00Z", data_source="yahoo",
        context="(package)", knowledge="(knowledge)", lessons="1. Fade RSI 70 breakouts in sideways markets.",
        trader="(trader)", claim_keys="breakout")
    assert "SHARED LESSONS" in prompt and "Fade RSI 70 breakouts" in prompt


def test_reflection_prompt_labels_each_forecast_with_its_model():
    prompt = PromptLibrary.ai_reflection(current_lessons="none yet", track_record="n/a",
                                         model_records="qwen: 3/5 correct; claude-opus-5-5: 4/5 correct",
                                         graded="- [claude-opus-5-5] Acme ...")
    assert "claude-opus-5-5: 4/5 correct" in prompt and "shared with every model" in prompt
