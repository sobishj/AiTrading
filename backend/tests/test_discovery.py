"""Daily discovery: NSE list parsing, data checks, AI review effects and list stickiness — no DB, no network."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery_service import ai_points, clean_name, eligibility, is_vetoed, parse_constituents, select  # noqa: E402

CSV = """﻿Company Name,Industry,Symbol,Series,ISIN Code
360 ONE WAM Ltd.,Financial Services,360ONE,EQ,INE466L01038
Mahindra & Mahindra Ltd.,Automobile and Auto Components,M&M,EQ,INE101A01026
Some Trade Co Limited,Textiles,TTOT,BE,INE000000000
Bad Row Ltd.,Power,BAD SYMBOL!,EQ,INE000000001
"""


def bars(n=250, close=500.0, volume=200_000, end="2026-09-30"):
    dates = pd.bdate_range(end=end, periods=n, tz="Asia/Kolkata")
    closes = np.full(n, close)
    return pd.DataFrame({"date": dates, "open": closes, "high": closes * 1.01, "low": closes * 0.99,
                         "close": closes, "volume": np.full(n, volume)})


def test_nse_list_is_parsed_with_clean_names_and_app_sectors():
    rows = parse_constituents(CSV)
    assert [r["symbol"] for r in rows] == ["360ONE", "M&M", "TTOT"]       # invalid symbol dropped
    assert rows[1] == {"symbol": "M&M", "name": "Mahindra & Mahindra", "sector": "Auto", "series": "EQ"}
    assert rows[0]["sector"] == "Financials" and rows[2]["series"] == "BE"
    assert clean_name("ABB India Ltd.") == "ABB India" and clean_name("Foo Limited") == "Foo"


def test_data_checks_filter_untradeable_or_untrustworthy_shares():
    today = date(2026, 10, 1)
    assert eligibility(bars(), today, 10.0) is None                         # Rs 500 x 2 lakh = Rs 10 cr/day
    assert eligibility(bars(n=120), today, 10.0) == "too little history"
    assert eligibility(bars(end="2026-09-18"), today, 10.0) == "stale or suspended"
    assert eligibility(bars(close=15.0, volume=10_000_000), today, 10.0) == "price below Rs 20"
    assert eligibility(bars(volume=100_000), today, 10.0) == "illiquid"     # Rs 5 cr/day
    assert eligibility(pd.DataFrame(), today, 10.0) == "no market data"


def test_ai_review_moves_scores_and_vetoes_only_confident_sells():
    assert ai_points(90) == 10 and ai_points(10) == -10 and ai_points(50) == 0 and ai_points(None) == 0
    assert ai_points(70) == 5
    assert is_vetoed({"signal": "SELL", "evidence_confidence": 65})
    assert not is_vetoed({"signal": "SELL", "evidence_confidence": 40})     # weak case: score only
    assert not is_vetoed({"signal": "HOLD", "evidence_confidence": 90})
    assert not is_vetoed(None)


def test_list_is_sticky_but_takes_the_best_newcomers():
    ordered = [f"S{i}" for i in range(1, 11)]               # best first
    # S9 was on yesterday's list and still ranks within keep_rank=9: it stays, S5 (5th best) makes way.
    assert select(ordered, incumbents={"S9"}, size=5, keep_rank=9) == ["S1", "S2", "S3", "S4", "S9"]
    # S10 has fallen outside keep_rank: replaced by the best newcomers.
    assert select(ordered, incumbents={"S10"}, size=5, keep_rank=9) == ["S1", "S2", "S3", "S4", "S5"]
    assert select(ordered, incumbents=set(), size=20, keep_rank=28) == ordered
