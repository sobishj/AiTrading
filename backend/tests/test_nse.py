"""Official NSE data: filings, calendar, ban list, bhavcopy, deals, and how they reach scores — no network."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery_service import eligibility  # noqa: E402
from nse_service import (delivery_view, parse_ban, parse_bhavcopy, parse_calendar, parse_deals,  # noqa: E402
                         parse_filing)


def filing(desc, text, symbol="ACME", name="Acme Industries Limited"):
    return {"symbol": symbol, "desc": desc, "attchmntText": text, "sm_name": name, "an_dt": "01-Oct-2026 13:20:36",
            "attchmntFile": "https://nsearchives.nseindia.com/corporate/x.pdf"}


def test_material_filings_become_news_tied_to_the_share():
    item = parse_filing(filing("Bagging/Receiving of orders/contracts",
                               "Acme Industries Limited has informed the Exchange about receipt of an order worth "
                               "Rs 1,200 crore from NHAI"))
    assert item["symbol"] == "ACME" and item["source"] == "NSE filing" and item["sentiment"] == 1
    assert item["title"] == ("Acme Industries: Bagging/Receiving of orders/contracts — receipt of an order worth "
                             "Rs 1,200 crore from NHAI")
    assert item["published"].startswith("2026-10-01T13:20:36+05:30")
    auditor = parse_filing(filing("Resignation of Statutory Auditor", "Acme has informed the Exchange about it"))
    assert auditor["sentiment"] == -1


def test_routine_paperwork_is_skipped():
    for desc in ("Trading Window", "Shareholders meeting", "Copy of Newspaper Publication", "ESOP/ESOS/ESPS",
                 "Analysts/Institutional Investor Meet/Con. Call Updates", "Loss of Share Certificates"):
        assert parse_filing(filing(desc, "x has informed the Exchange")) is None


def test_results_dates_ban_list_and_deals():
    cal = parse_calendar([{"symbol": "ACME", "purpose": "Financial Results", "bm_desc": "", "date": "08-Oct-2026"},
                          {"symbol": "ACME", "purpose": "Fund Raising", "bm_desc": "", "date": "03-Oct-2026"},
                          {"symbol": "BAD", "purpose": "x", "date": "not a date"}])
    assert [(e["date"], e["results"]) for e in cal["ACME"]] == [(date(2026, 10, 8), True), (date(2026, 10, 3), False)]
    assert "BAD" not in cal
    assert parse_ban("Securities in Ban For Trade Date 01-OCT-2026:\n1,BANDHANBNK\n2,SAIL\n") == \
        (date(2026, 10, 1), {"BANDHANBNK", "SAIL"})
    deals = parse_deals({"BULK_DEALS_DATA": [{"symbol": "ACME", "buySell": "BUY", "clientName": "Fund A",
                                              "qty": "144423", "watp": "100.01", "date": "30-Sep-2026"}]})
    assert deals["ACME"][0] == {"kind": "bulk", "side": "BUY", "client": "Fund A", "qty": 144423.0, "price": 100.01,
                                "date": "30-Sep-2026"}


BHAV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
20MICRONS, EQ, 30-Sep-2026, 212.80, 211.29, 221.80, 211.29, 214.00, 214.59, 217.09, 76779, 166.68, 3229, 34719, 45.22
GOLDBEES, ETF, 30-Sep-2026, 60.1, 60, 61, 59, 60.5, 60.5, 60.4, 100, 1, 1, -, -
TTOT, BE, 30-Sep-2026, 39.37, 40.00, 40.00, 38.59, 38.69, 38.67, 39.53, 901, 0.36, 24, -, -
"""


def test_bhavcopy_keeps_equity_rows_with_delivery():
    rows = parse_bhavcopy(BHAV)
    assert [r["symbol"] for r in rows] == ["20MICRONS", "TTOT"]          # ETF series skipped
    assert rows[0] == {"trade_date": date(2026, 9, 30), "symbol": "20MICRONS", "series": "EQ", "prev_close": 212.8,
                       "open": 211.29, "high": 221.8, "low": 211.29, "close": 214.59, "volume": 76779,
                       "deliv_qty": 34719, "deliv_pct": 45.22}
    assert rows[1]["deliv_pct"] is None


def test_delivery_is_compared_with_its_own_average():
    rows = [{"trade_date": date(2026, 9, 30), "close": 110.0, "prev_close": 105.0, "deliv_pct": 60.0}] + \
           [{"trade_date": date(2026, 9, 29), "close": 105.0, "prev_close": 105.0, "deliv_pct": 40.0}] * 20
    view = delivery_view(rows)
    assert view["deliv_avg_20"] == 40.0 and view["deliv_ratio"] == 1.5 and round(view["change_pct"], 2) == 4.76
    assert delivery_view(rows[:5])["deliv_ratio"] is None               # too few sessions to judge


def bars(n=250, close=500.0, end="2026-09-30"):
    dates = pd.bdate_range(end=end, periods=n, tz="Asia/Kolkata")
    closes = np.full(n, close)
    return pd.DataFrame({"date": dates, "open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": np.full(n, 400_000)})


def test_feed_prices_must_agree_with_nse():
    today = date(2026, 10, 1)
    assert eligibility(bars(), today, 10.0, official=(date(2026, 9, 30), 502.0)) is None     # 0.4% apart
    assert eligibility(bars(), today, 10.0, official=(date(2026, 9, 30), 520.0)) == "price disagrees with NSE"
    # A date the feed has no bar for (holiday) has nothing to compare against.


def test_exchange_facts_adjust_scores_with_reasons():
    from types import SimpleNamespace
    from ranking_service import RankingService

    item = SimpleNamespace(tags=[], exchange={
        "results_date": date(2026, 10, 8), "fo_ban": True,
        "delivery": {"deliv_pct": 60.0, "deliv_avg_20": 40.0, "deliv_ratio": 1.5, "change_pct": 4.8}})
    adjustments = {}
    RankingService._apply_exchange_facts(item, adjustments)
    # Not measured yet: a weak, labelled prior.
    assert adjustments == {"results_risk": -3.0, "fo_ban": -2.0, "delivery": 1.0}
    assert any("Results due 08 Oct" in t for t in item.tags) and any("not yet measured" in t for t in item.tags)
    quiet = SimpleNamespace(tags=[], exchange={"delivery": {"deliv_pct": 41, "deliv_ratio": 1.02, "change_pct": 3}})
    adjustments = {}
    RankingService._apply_exchange_facts(quiet, adjustments)
    assert adjustments == {} and quiet.tags == []


def test_missing_feed_sessions_are_filled_from_nse_but_never_across_a_split():
    from nse_service import NSEService

    feed = bars(n=5, close=100.0, end="2026-10-01").drop(index=3).reset_index(drop=True)   # 30 Sep missing
    svc = NSEService()
    official = {"trade_date": date(2026, 9, 30), "open": 101.0, "high": 104.0, "low": 100.0, "close": 103.0,
                "volume": 5000}
    svc._sessions = {"ACME": [official]}
    out = svc.fill_missing_sessions("ACME", feed)
    assert len(out) == 5 and out.attrs["nse_filled"] == [date(2026, 9, 30)]
    row = out.iloc[3]
    assert str(row["date"])[:10] == "2026-09-30" and row["close"] == 103.0 and row["high"] == 104.0
    # An official price on another scale (a split the feed has already adjusted for) is not mixed in.
    svc._sessions = {"ACME": [{**official, "open": 51.0, "high": 52.0, "low": 50.0, "close": 51.5}]}
    assert len(svc.fill_missing_sessions("ACME", feed)) == 4
    # Sessions outside the feed's own range (before its first bar / after its last) are left alone.
    svc._sessions = {"ACME": [{**official, "trade_date": date(2026, 10, 2)}]}
    assert len(svc.fill_missing_sessions("ACME", feed)) == 4


def test_delivery_adjustment_follows_what_history_measured():
    from types import SimpleNamespace
    from nse_service import nse_service
    from ranking_service import RankingService

    def run(edge):
        nse_service.delivery_edges["delivery_buying"] = edge
        item = SimpleNamespace(tags=[], exchange={"delivery": {"deliv_pct": 60.0, "deliv_ratio": 1.5, "change_pct": 2.0}})
        adjustments = {}
        RankingService._apply_exchange_facts(item, adjustments)
        return adjustments.get("delivery"), item.tags[-1]

    try:
        points, tag = run({"n": 900, "rate": 58.0, "base": 51.0, "edge": 7.0})     # measured edge: 7 pts -> 2.8
        assert points == 2.8 and "58% of 900" in tag
        points, tag = run({"n": 900, "rate": 49.0, "base": 51.0, "edge": -2.0})    # history says no edge -> 0
        assert points is None and "historically no edge" in tag
        points, _ = run({"n": 900, "rate": 80.0, "base": 51.0, "edge": 29.0})      # capped
        assert points == 3.0
    finally:
        nse_service.delivery_edges["delivery_buying"] = None
