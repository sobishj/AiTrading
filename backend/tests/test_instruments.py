"""Instrument search, index history and option chains — parsing and the numbers shown; no DB, no network."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instruments_service import (InstrumentsService, parse_equity_list, parse_etf_list, parse_fo_lots,  # noqa: E402
                                 parse_index_closes)
from options_service import max_pain, parse_chain, summarize  # noqa: E402

EQUITY = """SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE
HDFCBANK,HDFC Bank Limited,EQ,08-NOV-1995,1,1,INE040A01034,1
HDFCLIFE,HDFC Life Insurance Company Limited,EQ,17-NOV-2017,10,1,INE795G01014,10
HDFCAMC,HDFC Asset Management Company Limited,EQ,06-AUG-2018,5,1,INE127D01025,5
BAJAJFINSV,Bajaj Finserv Limited,EQ,26-MAY-2008,1,1,INE918I01026,1
KOTAKBANK,Kotak Mahindra Bank Limited,EQ,20-DEC-2001,5,1,INE237A01028,5
"""
ETF = """Symbol,Underlying Asset,SecurityName,DateofListing,MarketLot,ISINNumber,FaceValue,ETF Underlying,Underlying Key
NIFTYBEES,Nifty 50,NIPINDETFNIFTYBEES,08-Jan-02,1,INF204KB14I2,1,EQUITY,Nifty 50
"""
LOTS = """UNDERLYING                          ,SYMBOL    ,OCT-26     ,NOV-26
NIFTY 50                            ,NIFTY     ,65         ,65
HDFC BANK LIMITED                   ,HDFCBANK  ,550        ,550
"""


def service():
    s = InstrumentsService()
    s._set(parse_equity_list(EQUITY) + parse_etf_list(ETF), parse_fo_lots(LOTS))
    return s


def test_search_finds_shares_by_name_and_explains_what_isnt_listed():
    s = service()
    hdfc = s.search("hdfc")
    assert [r["symbol"] for r in hdfc["results"][:3]] == ["HDFCBANK", "HDFCAMC", "HDFCLIFE"] or \
        {r["symbol"] for r in hdfc["results"][:3]} == {"HDFCBANK", "HDFCLIFE", "HDFCAMC"}
    assert "merged into HDFC Bank" in hdfc["note"]
    nse = s.search("nse")
    assert "isn't a listed company" in nse["note"] and all(r["symbol"] != "BAJAJFINSV" for r in nse["results"])
    assert s.search("nifty")["results"][0]["symbol"] == "NIFTY"
    assert s.search("bank nifty")["results"][0]["symbol"] == "BANKNIFTY"
    assert "No share, ETF or index" in s.search("qwertyzz")["note"]


def test_lookup_knows_types_and_options():
    s = service()
    assert s.lookup("HDFCBANK") == {"symbol": "HDFCBANK", "name": "HDFC Bank", "type": "equity", "series": "EQ",
                                    "has_options": True, "lot_size": 550}
    assert s.lookup("NIFTY")["type"] == "index" and s.lookup("NIFTY")["has_options"]
    assert s.lookup("NIFTYBEES")["type"] == "etf" and not s.lookup("NIFTYBEES")["has_options"]
    assert s.lookup("HDFC") is None and s.lookup("NSE") is None


def test_official_index_history_is_parsed_for_known_indices_only():
    text = """Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield
Nifty 50,05-10-2026,22532.4,22621.8,22397.1,22555.75,133.8,.6,412554239,33398.25,19.3,2.77,1.23
Nifty Financial Services,05-10-2026,24736.05,24881.4,24517.85,24656.4,100.3,.41,190624504,15432.49,15.11,2.28,1.01
Some Unknown Index,05-10-2026,1,1,1,1,0,0,0,0,0,0,0
"""
    rows = parse_index_closes(text)
    assert [(r["symbol"], r["trade_date"], r["close"], r["series"]) for r in rows] == [
        ("NIFTY", date(2026, 10, 5), 22555.75, "IDX"), ("FINNIFTY", date(2026, 10, 5), 24656.4, "IDX")]
    assert rows[0]["high"] == 22621.8 and rows[0]["low"] == 22397.1


def chain_rows():
    # strike, CE OI, PE OI
    return [{"strike": k, "ce_oi": c, "pe_oi": p, "ce_iv": 14.0 if k == 22550 else None,
             "pe_iv": 13.0 if k == 22550 else None}
            for k, c, p in ((22400, 100, 900), (22500, 300, 1200), (22550, 500, 700), (22600, 1500, 200),
                            (22700, 900, 50))]


def test_option_summary_numbers():
    s = summarize(chain_rows(), underlying=22573.5)
    assert s["pcr"] == round(3050 / 3300, 2)
    assert s["atm_strike"] == 22550 and s["atm_iv"] == 13.5
    assert s["support"] == 22500 and s["resistance"] == 22600       # most PE OI below / CE OI above the price
    # Max pain: brute-force check that no other strike costs option buyers less.
    def pain(price):
        return sum(r["ce_oi"] * max(0, price - r["strike"]) + r["pe_oi"] * max(0, r["strike"] - price) for r in chain_rows())
    assert pain(max_pain(chain_rows())) == min(pain(r["strike"]) for r in chain_rows())


def test_chain_parsing_keeps_one_expiry_and_reads_both_sides():
    payload = {"records": {"underlyingValue": 22573.5, "timestamp": "06-Oct-2026 09:21:08", "data": [
        {"expiryDates": "06-Oct-2026", "strikePrice": 22550,
         "CE": {"lastPrice": 60.5, "openInterest": 500, "changeinOpenInterest": -20, "impliedVolatility": 14.0},
         "PE": {"lastPrice": 38.0, "openInterest": 700, "changeinOpenInterest": 45, "impliedVolatility": 13.0}},
        {"expiryDates": "13-Oct-2026", "strikePrice": 22550, "CE": {"lastPrice": 150}, "PE": {"lastPrice": 120}},
    ]}}
    parsed = parse_chain(payload, "06-Oct-2026")
    assert parsed["underlying"] == 22573.5 and parsed["timestamp"] == "06-Oct-2026 09:21:08"
    assert len(parsed["rows"]) == 1
    row = parsed["rows"][0]
    assert (row["ce_ltp"], row["ce_oi_change"], row["pe_ltp"], row["pe_oi"]) == (60.5, -20.0, 38.0, 700.0)
