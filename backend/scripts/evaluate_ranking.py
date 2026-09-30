"""
Walk-forward evaluation of the ranking engine's daily pick.

For every past trading day it scores each universe stock exactly as the live
engine does (analysis_service.snapshot_at + RankingService's strategy/evidence
step, using only bars up to that day), takes the day's #1 BUY the way the
morning brief does, enters at the next session's open and measures what
followed: 5- and 10-day return, return vs NIFTY, and whether the plan's target
or stop was hit first within 10 sessions.

It compares the current engine with the legacy one (no entry-timing adjustment,
no NIFTY trend gate), split into 2020-23 (where the timing scales were fitted)
and 2024 onward (held out). Not replayed: news sentiment (held neutral), AI
view, learned edge and the per-stock historical edge, which need live data.

    cd backend
    venv/Scripts/python scripts/evaluate_ranking.py [--days 2600] [--cache candles.pkl]
"""
import argparse
import asyncio
import os
import pickle
import sys
from contextlib import contextmanager

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ranking_service as rs_module  # noqa: E402
from analysis_service import analysis_service  # noqa: E402
from data_provider import yahoo_provider  # noqa: E402
from ranking_service import RankedStock, RankingContext, RankingService  # noqa: E402
from universe import UNIVERSE  # noqa: E402

BENCHMARK = "^NSEI"
WARMUP_BARS = 260          # enough for EMA-200 and 12-1 month momentum
TEST_FROM_YEAR = 2024
PLAN_BARS = 10
NEUTRAL_SENTIMENT = {"score": 50.0, "sentiment": "neutral", "news_count": 0, "earnings_news": False}


async def fetch(days: int) -> dict[str, pd.DataFrame]:
    out = {}
    for symbol in [BENCHMARK] + [u[0] for u in UNIVERSE]:
        out[symbol] = await yahoo_provider.get_candles(symbol, days=days)
    await yahoo_provider.close()
    return out


def session_dates(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["date"]).dt.tz_convert("Asia/Kolkata").dt.date


# label -> (timing weight, timing cap, NIFTY gate); None = the live constants.
VARIANTS = {
    "legacy": (0.0, 0.0, False),     # the engine before entry timing and the NIFTY gate
    "current": (None, None, True),
}


@contextmanager
def timing_constants(weight, cap):
    saved = rs_module.TIMING_WEIGHT, rs_module.MAX_TIMING_ADJUSTMENT
    if weight is not None:
        rs_module.TIMING_WEIGHT = weight
    if cap is not None:
        rs_module.MAX_TIMING_ADJUSTMENT = cap
    try:
        yield
    finally:
        rs_module.TIMING_WEIGHT, rs_module.MAX_TIMING_ADJUSTMENT = saved


def plan_outcome(plan, entry: float, highs, lows, closes, i: int) -> float:
    """+1 target first (or up at the time limit), -1 stop first (or down), nan if not enough bars."""
    n = len(closes)
    if i + PLAN_BARS >= n:
        return np.nan
    if entry <= plan.stop_loss:
        return -1.0
    for j in range(i + 1, i + 1 + PLAN_BARS):
        if lows[j] <= plan.stop_loss:
            return -1.0
        if highs[j] >= plan.target:
            return 1.0
    return 1.0 if closes[i + PLAN_BARS] > entry else -1.0


def replay(data: dict[str, pd.DataFrame], variants: dict = VARIANTS) -> dict[str, pd.DataFrame]:
    nifty = analysis_service.indicator_frame(data[BENCHMARK])
    nifty.index = session_dates(nifty)
    n_open, n_close = nifty["open"], nifty["close"]
    nifty_fwd = {h: (n_close.shift(-h) / n_open.shift(-1) - 1) * 100 for h in (5, 10)}

    snapshots = []  # (date, symbol, snapshot, forward-looking arrays)
    for symbol, name, sector, _ in UNIVERSE:
        raw = data.get(symbol)
        if raw is None or len(raw) <= WARMUP_BARS + PLAN_BARS:
            continue
        df = analysis_service.indicator_frame(raw)
        dates = session_dates(df).to_numpy()
        flags = analysis_service.setup_flags(df)
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        for i in range(WARMUP_BARS, len(df) - 1):
            d = dates[i]
            if d not in nifty.index:
                continue
            snap = analysis_service.snapshot_at(symbol, df, flags, i, nifty.at[d, "return_20d"])
            snapshots.append((d, symbol, name, sector, snap, i, o, h, l, c))
        print(f"  scored {symbol}", flush=True)

    service = RankingService()
    results = {}
    for label, (weight, cap, gated) in variants.items():
        rows = []
        with timing_constants(weight, cap):
            for d, symbol, name, sector, snap, i, o, h, l, c in snapshots:
                context = RankingContext(market_uptrend=(not gated) or bool(n_close[d] >= nifty.at[d, "ema_50"]))
                base, volume_component = service._composite_score(snap, NEUTRAL_SENTIMENT, context.weights)
                item = RankedStock(stock_id=0, symbol=symbol, name=name, sector=sector, conviction_score=base,
                                   base_score=base, technical=snap, sentiment=NEUTRAL_SENTIMENT,
                                   volume_score=volume_component)
                service._apply_strategy_and_evidence(item, context)
                if item.action != "BUY":
                    continue
                entry = o[i + 1]
                row = {"date": d, "symbol": symbol, "conviction": item.conviction_score, "strategy": item.strategy,
                       "plan": plan_outcome(item.plan, entry, h, l, c, i)}
                for horizon in (5, 10):
                    ret = (c[i + horizon] / entry - 1) * 100 if i + horizon < len(c) else np.nan
                    row[f"ret{horizon}"] = ret
                    row[f"ex{horizon}"] = ret - nifty_fwd[horizon].get(d, np.nan)
                rows.append(row)
        results[label] = pd.DataFrame(rows)
    return results


def summarize(label: str, picks: pd.DataFrame) -> None:
    picks = picks.dropna(subset=["ret5"])
    if picks.empty:
        print(f"{label:28s} no picks")
        return
    print(f"{label:28s} picks={len(picks):4d}  up after 5d={100 * (picks.ret5 > 0).mean():5.1f}%  "
          f"avg 5d={picks.ret5.mean():+.2f}%  vs NIFTY 5d={picks.ex5.mean():+.2f}%  "
          f"10d={picks.ex10.mean():+.2f}%  plan win={100 * (picks.plan == 1).mean():5.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=2600, help="calendar days of history to fetch")
    parser.add_argument("--cache", help="pickle file to reuse/store fetched candles")
    args = parser.parse_args()

    if args.cache and os.path.exists(args.cache):
        with open(args.cache, "rb") as fh:
            data = pickle.load(fh)
    else:
        print("Fetching candles from Yahoo...")
        data = asyncio.run(fetch(args.days))
        if args.cache:
            with open(args.cache, "wb") as fh:
                pickle.dump(data, fh)

    results = replay(data)
    for label, buys in results.items():
        top = buys.sort_values("conviction", ascending=False).groupby("date").head(1)
        years = pd.to_datetime(top["date"]).dt.year
        print(f"\n=== {label} engine: daily #1 BUY pick ===")
        summarize("all years", top)
        summarize(f"fit period (<{TEST_FROM_YEAR})", top[years < TEST_FROM_YEAR])
        summarize(f"held out ({TEST_FROM_YEAR}+)", top[years >= TEST_FROM_YEAR])
        for year in sorted(years.unique()):
            summarize(f"  {year}", top[years == year])


if __name__ == "__main__":
    main()
