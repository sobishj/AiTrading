"""Trades reported in chat (parser) and the trading-style profile (pure aggregation) — no DB, no network."""
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade_intent import parse  # noqa: E402
from trader_profile_service import Entry, Exit, prompt_block, replay_entry, summarize  # noqa: E402

TODAY = date(2026, 9, 30)


def actions(text):
    r = parse(text, TODAY)
    return None if r is None else [(a.side, a.quantity, a.price, a.trade_date) for a in r.actions]


def test_round_trip_without_quantity_leaves_it_empty():
    assert actions("i bought this share at 12 and sold at 50") == [("BUY", None, 12.0, None), ("SELL", None, 50.0, None)]


def test_quantity_date_and_formats():
    assert actions("Bought 10 ITC @ 265.5 yesterday") == [("BUY", 10.0, 265.5, date(2026, 9, 29))]
    assert actions("sold 5 shares at 280 on 25 Sep") == [("SELL", 5.0, 280.0, date(2026, 9, 25))]
    assert actions("I bought 8 Eternal at 331.55 on 28/09/2026") == [("BUY", 8.0, 331.55, date(2026, 9, 28))]
    assert actions("booked profit at ₹1,820 today") == [("SELL", None, 1820.0, TODAY)]
    # the sale reuses the bought quantity
    assert actions("bought 100 qty at rs 45, sold at rs 52") == [("BUY", 100.0, 45.0, None), ("SELL", 100.0, 52.0, None)]


def test_questions_and_chatter_are_not_trades():
    for text in ["should I buy at 250?", "what is the target for ITC", "Is it good to sell at 300",
                 "I bought it", "tell me about the market"]:
        assert actions(text) is None, text


def entry(symbol, factors=(), style=(), fwd10=None, alignment="none"):
    e = Entry(symbol, symbol, "IT", 1, date(2026, 9, 1), 100.0, 10, "manual", list(factors), list(style))
    if fwd10 is not None:
        e.fwd[10] = fwd10
    e.ai_alignment = alignment
    return e


def test_patterns_need_enough_trades():
    one = summarize([entry("A", ["uptrend"])], [], [])
    assert one["stats"]["entries"] == 1
    assert any("need at least 3" in n for n in one["style_notes"])
    assert not any("uptrend" in n for n in one["style_notes"])


def test_style_statements_come_from_measured_trades():
    entries = [entry(s, ["uptrend", "near_52w_high"], ["chase"], fwd10=-2.5, alignment="against") for s in "ABCD"]
    entries += [entry("E", [], ["dip_buy"], fwd10=3.0, alignment="followed")]
    exits = [Exit("A", date(2026, 9, 5), 105, 10, 5.0, 50, 4, after_10d_pct=6.0),
             Exit("B", date(2026, 9, 6), 104, 10, 4.0, 40, 5, after_10d_pct=4.0),
             Exit("C", date(2026, 9, 20), 90, 10, -10.0, -100, 19, after_10d_pct=-1.0),
             Exit("D", date(2026, 9, 25), 92, 10, -8.0, -80, 24, after_10d_pct=0.5)]
    p = summarize(entries, exits, [])
    notes = " ".join(p["style_notes"])
    assert "Usually buys stocks already in an uptrend: 4 of 5 buys (early sign)" in notes
    assert "Chases strength: 4 of 5" in notes
    assert "Holds losers longer than winners" in notes
    assert "bigger than average win" in notes
    assert "Tends to sell early: after 2 of 4 sales" in notes
    assert p["stats"]["win_rate"] == 50.0 and p["stats"]["payoff_ratio"] == 0.5


def test_prompt_block_includes_current_holding_and_handles_empty():
    assert "No trades recorded yet" in prompt_block(None)
    profile = summarize([entry("ITC")], [], [])
    profile["holdings"] = [{"symbol": "ITC", "quantity": 10, "avg_price": 265.0, "stop_loss": 255, "target": None}]
    profile["entries"] = [{"symbol": "ITC", "day": "2026-09-01", "price": 265.0, "fwd": {"10": 2.0}}]
    text = prompt_block(profile, "ITC")
    assert "currently holds 10 ITC" in text and "10 days later +2.0%" in text


def test_replay_entry_reads_real_bars():
    days = pd.date_range("2026-06-01", periods=120, freq="B")
    closes = [100 + i * 0.5 for i in range(120)]
    df = pd.DataFrame({"date": days, "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                       "close": closes, "volume": [1000] * 120})
    from analysis_service import analysis_service
    frame = analysis_service.indicator_frame(df)
    e = Entry("X", "X", None, 1, days[100].date(), closes[100] + 0.9, 5, "manual")
    replay_entry(e, frame)
    assert "uptrend" in e.factors and "near_day_high" in e.style
    assert e.fwd[10] == round((closes[110] / e.price - 1) * 100, 2)
    assert e.ret_5d_before == round((closes[100] / closes[95] - 1) * 100, 2)


def test_prices_outside_the_real_range_are_excluded():
    from trader_profile_service import price_mismatch
    df = pd.DataFrame({"date": pd.to_datetime(["2026-09-30"]), "low": [325.0], "high": [336.0]})
    assert price_mismatch(df, date(2026, 9, 30), 332.6, "ETERNAL sale") is None
    issue = price_mismatch(df, date(2026, 9, 30), 3326.2, "ETERNAL sale")
    assert "traded Rs 325.00-336.00" in issue
    assert price_mismatch(df, date(2026, 9, 29), 3326.2, "x") is None      # no bar -> can't check
    bad = Exit("ETERNAL", date(2026, 9, 30), 3326.2, 8, 903.2, 23957, 2, price_issue=issue)
    p = summarize([entry("ETERNAL")], [bad], [])
    assert p["stats"]["closed_trades"] == 0 and p["stats"]["excluded_trades"] == 1
    assert p["style_notes"][0].startswith("Excluded from learning until corrected")
