"""
Technical analysis engine.

From daily OHLCV candles it computes:
- indicators: RSI, MACD, EMA 20/50/200, ATR, volume ratio, 52-week range,
  20/60-day returns and relative strength against NIFTY 50;
- a composite 0-100 technical score built from trend, momentum, price
  structure and volume, minus penalties for overbought or overextended moves;
- setup detection for the Strategy Library (Breakout, Gap-up Continuation,
  Pullback to EMA, Trend Momentum; Earnings Momentum and Sector Rotation need
  news/sector context and are tagged in ranking_service);
- a historical back-test of the detected setup on the stock's own history, so
  every pick carries evidence of how that pattern played out before;
- a trade plan (action, entry zone, ATR-based stop, R-multiple target,
  holding period, risk level).

Everything here is deterministic and fast: no network, no LLM.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
SESSION_MINUTES = 375

TICK = 0.05

# Strategy names, in detection priority order. They match the PRD's Strategy
# Library and are persisted on each recommendation, so don't rename casually.
BREAKOUT = "Breakout"
GAP_UP = "Gap-up Continuation"
PULLBACK = "Pullback to EMA"
MOMENTUM = "Trend Momentum"
EARNINGS_MOMENTUM = "Earnings Momentum"
SECTOR_ROTATION = "Sector Rotation"
NO_SETUP = "No Setup"

PRICE_SETUPS = [BREAKOUT, GAP_UP, PULLBACK, MOMENTUM]
ALL_STRATEGIES = [BREAKOUT, EARNINGS_MOMENTUM, GAP_UP, PULLBACK, MOMENTUM, SECTOR_ROTATION]

# Per-strategy plan shape: reward multiple (R) and holding period. With the
# stop capped at PLAN_MAX_STOP_ATR, a 2R target is ~3 ATR — the same shape the
# historical back-test measures, so its win rate is evidence for this plan.
PLAN_MAX_STOP_ATR = 1.5
STRATEGY_PROFILE: dict[str, tuple[float, str]] = {
    BREAKOUT: (2.0, "3-10 trading days"),
    GAP_UP: (1.5, "1-3 trading days"),
    PULLBACK: (2.0, "5-10 trading days"),
    MOMENTUM: (2.0, "5-10 trading days"),
    EARNINGS_MOMENTUM: (2.0, "3-10 trading days"),
    SECTOR_ROTATION: (2.0, "5-15 trading days"),
    NO_SETUP: (2.0, "5-10 trading days"),
}

# Back-test exit rules (in ATRs from entry) and max bars held.
BACKTEST_STOP_ATR = 1.5
BACKTEST_TARGET_ATR = 3.0
BACKTEST_MAX_BARS = 10
BACKTEST_COOLDOWN_BARS = 5


@dataclass
class SetupStats:
    """How a setup played out historically on this stock (daily bars)."""
    strategy: str
    signals: int
    wins: int
    losses: int
    win_rate: Optional[float]
    avg_return_pct: Optional[float]


@dataclass
class TechnicalSnapshot:
    symbol: str
    rsi: Optional[float]
    macd: Optional[float]
    macd_signal: Optional[float]
    ema_20: Optional[float]
    ema_50: Optional[float]
    volume: Optional[int]
    avg_volume_20: Optional[float]
    volume_ratio: Optional[float]
    support: Optional[float]
    resistance: Optional[float]
    volume_spike: bool
    technical_score: float
    signal: str
    # --- added in v2 (defaults keep older call sites working) ---
    close: Optional[float] = None
    open: Optional[float] = None
    prev_close: Optional[float] = None
    change_pct: Optional[float] = None
    ema_200: Optional[float] = None
    macd_hist: Optional[float] = None
    atr: Optional[float] = None
    atr_pct: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    return_20d: Optional[float] = None
    return_60d: Optional[float] = None
    relative_strength_20d: Optional[float] = None
    swing_low_10: Optional[float] = None
    last_bar_time: Optional[str] = None
    # --- added for the multi-model research context (all derived from the same daily bars) ---
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    vwap_20: Optional[float] = None       # 20-day VWAP from daily bars (typical price x volume), not intraday VWAP
    breakout: bool = False                # close above the prior 20-day high on >= 1.3x volume
    breakdown: bool = False               # close below the prior 20-day low on >= 1.3x volume
    setups: list[str] = field(default_factory=list)
    trend: str = "unknown"
    score_breakdown: dict = field(default_factory=dict)
    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    backtest: dict[str, SetupStats] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return self.close is not None


@dataclass
class TradePlan:
    action: str            # BUY / WAIT / AVOID
    instrument: str
    strategy: str
    entry_low: float
    entry_high: float
    stop_loss: float
    target: float
    risk_reward: float
    holding_period: str
    risk_level: str        # low / medium / high
    invalidation: str
    exit_logic: str

    @property
    def entry_mid(self) -> float:
        return round((self.entry_low + self.entry_high) / 2, 2)


def round_tick(price: float) -> float:
    return round(round(price / TICK) * TICK, 2)


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def session_fraction(bar_time: Optional[pd.Timestamp], now: Optional[datetime] = None) -> float:
    """
    Fraction of today's NSE session elapsed, if `bar_time` is today's
    still-forming daily bar; 1.0 otherwise. Used to scale partial-day volume
    so a 10:30 AM volume reading isn't compared raw against full-day averages.
    """
    if bar_time is None:
        return 1.0
    now = now or datetime.now(IST)
    bar_date = bar_time.tz_convert(IST).date() if bar_time.tzinfo else bar_time.date()
    if bar_date != now.date():
        return 1.0
    open_dt = datetime.combine(now.date(), MARKET_OPEN, IST)
    elapsed = (now - open_dt).total_seconds() / 60
    if elapsed >= SESSION_MINUTES:
        return 1.0
    return max(0.1, elapsed / SESSION_MINUTES)


class AnalysisService:
    """Computes indicators, scores, setups, back-tests and trade plans from OHLCV candles."""

    REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}

    # ------------------------------------------------------------------
    # Indicator frame (vectorised over the whole history)
    # ------------------------------------------------------------------
    def indicator_frame(self, candles: pd.DataFrame) -> pd.DataFrame:
        df = candles.copy()
        df.columns = [c.lower() for c in df.columns]
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"candles missing columns: {missing}")

        for col in ("open", "high", "low", "close"):
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].fillna(0).astype(float)
        close, high, low = df["close"], df["high"], df["low"]

        df["rsi"] = RSIIndicator(close=close, window=14).rsi()
        macd = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_hist"] = macd.macd_diff()
        df["ema_20"] = EMAIndicator(close=close, window=20).ema_indicator()
        df["ema_50"] = EMAIndicator(close=close, window=50).ema_indicator()
        df["ema_200"] = (EMAIndicator(close=close, window=200).ema_indicator()
                         if len(df) >= 200 else np.nan)
        df["sma_20"] = close.rolling(20, min_periods=20).mean()
        df["sma_50"] = close.rolling(50, min_periods=50).mean()
        typical = (high + low + close) / 3
        vol20 = df["volume"].rolling(20, min_periods=10).sum()
        df["vwap_20"] = (typical * df["volume"]).rolling(20, min_periods=10).sum() / vol20.replace(0, np.nan)
        df["atr"] = (AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
                     if len(df) >= 15 else np.nan)
        # ta fills the warm-up period with zeros rather than NaN.
        df.loc[df["atr"] == 0, "atr"] = np.nan

        df["prior_high_20"] = high.shift(1).rolling(20, min_periods=10).max()
        df["prior_low_20"] = low.shift(1).rolling(20, min_periods=10).min()
        df["swing_low_10"] = low.rolling(10, min_periods=5).min()
        df["avg_volume_20"] = df["volume"].shift(1).rolling(20, min_periods=5).mean()
        df["volume_ratio"] = df["volume"] / df["avg_volume_20"].replace(0, np.nan)
        df["high_52w"] = high.rolling(252, min_periods=20).max()
        df["low_52w"] = low.rolling(252, min_periods=20).min()
        df["return_20d"] = close.pct_change(20) * 100
        df["return_60d"] = close.pct_change(60) * 100
        return df

    @staticmethod
    def setup_flags(df: pd.DataFrame, volume_ratio: Optional[pd.Series] = None) -> pd.DataFrame:
        """Boolean column per price setup, for every bar (used live and in the back-test)."""
        vr = df["volume_ratio"] if volume_ratio is None else volume_ratio
        close, open_, low = df["close"], df["open"], df["low"]
        ema20, ema50, rsi = df["ema_20"], df["ema_50"], df["rsi"]
        flags = pd.DataFrame(index=df.index)
        flags[BREAKOUT] = (close > df["prior_high_20"]) & (vr >= 1.3) & (close >= ema20)
        flags[GAP_UP] = (
            (open_ >= df["high"].shift(1) * 1.005) & (close > open_) & (close >= close.shift(1) * 1.01)
        )
        flags[PULLBACK] = (
            (ema20 > ema50) & (close > ema50) & (low <= ema20 * 1.01) & (close >= ema20) & rsi.between(40, 60)
        )
        flags[MOMENTUM] = (
            (close > ema20) & (ema20 > ema50) & rsi.between(55, 72) & (df["macd"] > df["macd_signal"])
        )
        return flags.fillna(False)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def compute_indicators(self, symbol: str, candles: pd.DataFrame,
                           benchmark_return_20d: Optional[float] = None,
                           now: Optional[datetime] = None) -> TechnicalSnapshot:
        """
        candles: DataFrame sorted ascending with [open, high, low, close, volume]
        (and ideally a `date` column). ~250+ daily bars give the full picture;
        fewer still work, with EMA-200/52-week fields left empty.
        """
        if candles is None or candles.empty or len(candles) < 30:
            if candles is not None and not candles.empty:
                logger.warning("Only %d candles for %s — too few to analyse", len(candles), symbol)
            return self._empty_snapshot(symbol)

        df = self.indicator_frame(candles)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        bar_time = None
        if "date" in df.columns:
            bar_time = pd.Timestamp(last["date"])
        fraction = session_fraction(bar_time, now)

        volume = int(last["volume"])
        avg_volume = _f(last["avg_volume_20"])
        # Project today's partial volume to a full-session equivalent.
        volume_ratio = (volume / fraction / avg_volume) if avg_volume else None

        live_ratio = df["volume_ratio"].copy()
        if volume_ratio is not None:
            live_ratio.iloc[-1] = volume_ratio
        flags = self.setup_flags(df, live_ratio)
        setups = [name for name in PRICE_SETUPS if bool(flags[name].iloc[-1])]

        close = float(last["close"])
        ema20, ema50, ema200 = _f(last["ema_20"]), _f(last["ema_50"]), _f(last["ema_200"])
        atr = _f(last["atr"])
        return_20d = _f(last["return_20d"])
        relative_strength = (return_20d - benchmark_return_20d
                             if return_20d is not None and benchmark_return_20d is not None else None)

        snap = TechnicalSnapshot(
            symbol=symbol,
            rsi=_f(last["rsi"]),
            macd=_f(last["macd"]),
            macd_signal=_f(last["macd_signal"]),
            ema_20=ema20,
            ema_50=ema50,
            volume=volume,
            avg_volume_20=avg_volume,
            volume_ratio=round(volume_ratio, 2) if volume_ratio is not None else None,
            support=_f(last["prior_low_20"]),
            resistance=_f(last["prior_high_20"]),
            volume_spike=bool(volume_ratio and volume_ratio >= 2.0),
            technical_score=50.0,
            signal="neutral",
            close=close,
            open=float(last["open"]),
            prev_close=float(prev["close"]),
            change_pct=round((close / float(prev["close"]) - 1) * 100, 2),
            ema_200=ema200,
            macd_hist=_f(last["macd_hist"]),
            atr=atr,
            atr_pct=round(atr / close * 100, 2) if atr else None,
            high_52w=_f(last["high_52w"]),
            low_52w=_f(last["low_52w"]),
            return_20d=round(return_20d, 2) if return_20d is not None else None,
            return_60d=round(_f(last["return_60d"]), 2) if _f(last["return_60d"]) is not None else None,
            relative_strength_20d=round(relative_strength, 2) if relative_strength is not None else None,
            swing_low_10=_f(last["swing_low_10"]),
            last_bar_time=bar_time.isoformat() if bar_time is not None else None,
            setups=setups,
            sma_20=_f(last["sma_20"]),
            sma_50=_f(last["sma_50"]),
            vwap_20=_f(last["vwap_20"]),
        )
        prior_high, prior_low = _f(last["prior_high_20"]), _f(last["prior_low_20"])
        heavy = bool(volume_ratio is not None and volume_ratio >= 1.3)
        snap.breakout = bool(prior_high is not None and close > prior_high and heavy)
        snap.breakdown = bool(prior_low is not None and close < prior_low and heavy)
        snap.trend = self._trend(snap)
        self._score(snap, prev_hist=_f(prev["macd_hist"]))
        snap.backtest = self.backtest_setups(df, flags)
        return snap

    @staticmethod
    def _trend(s: TechnicalSnapshot) -> str:
        if s.ema_20 is None or s.ema_50 is None or s.close is None:
            return "unknown"
        if s.close > s.ema_20 > s.ema_50:
            return "uptrend"
        if s.close < s.ema_20 < s.ema_50:
            return "downtrend"
        return "sideways"

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _score(self, s: TechnicalSnapshot, prev_hist: Optional[float]) -> None:
        """
        0-100 technical score = trend (30) + momentum (25) + structure (20)
        + volume (15) + relative strength (10) - penalties. Also records the
        human-readable positives/negatives the Analysis panel shows.
        """
        pos, neg = s.positives, s.negatives
        close = s.close

        trend = 0.0
        if s.ema_20 is not None and close > s.ema_20:
            trend += 8
        if s.ema_20 is not None and s.ema_50 is not None and s.ema_20 > s.ema_50:
            trend += 8
            pos.append("EMA 20 above EMA 50 (short-term trend up)")
        elif s.ema_20 is not None and s.ema_50 is not None:
            neg.append("EMA 20 below EMA 50 (short-term trend down)")
        if s.ema_50 is not None and close > s.ema_50:
            trend += 6
        if s.ema_200 is not None:
            if close > s.ema_200:
                trend += 4
                pos.append("Price above the 200-day EMA (long-term uptrend)")
            else:
                neg.append("Price below the 200-day EMA (long-term downtrend)")
            if s.ema_50 is not None and s.ema_50 > s.ema_200:
                trend += 4
        elif s.ema_50 is not None and close > s.ema_50:
            trend += 4

        momentum = 0.0
        if s.rsi is not None:
            if 55 <= s.rsi <= 68:
                momentum += 12
                pos.append(f"RSI {s.rsi:.0f}: strong but not overbought")
            elif 50 <= s.rsi < 55 or 68 < s.rsi <= 72:
                momentum += 8
            elif 45 <= s.rsi < 50:
                momentum += 4
            elif s.rsi < 40:
                neg.append(f"RSI {s.rsi:.0f}: weak momentum")
        if s.macd is not None and s.macd_signal is not None:
            if s.macd > s.macd_signal:
                momentum += 7
                pos.append("MACD above its signal line")
            else:
                neg.append("MACD below its signal line")
        if s.macd_hist is not None and prev_hist is not None and s.macd_hist > prev_hist:
            momentum += 3
        if s.return_20d is not None and s.return_20d > 0:
            momentum += 3

        structure = 0.0
        if BREAKOUT in s.setups:
            structure += 10
            pos.append(f"Broke above the 20-day high ({s.resistance:.2f})")
        if s.high_52w and close >= s.high_52w * 0.95:
            structure += 6
            pos.append(f"Within 5% of the 52-week high ({s.high_52w:.2f})")
        if PULLBACK in s.setups:
            structure += 6
            pos.append("Pulled back to a rising EMA 20 and held")
        if GAP_UP in s.setups:
            structure += 4
            pos.append("Gapped up and held the gap")
        structure = min(structure, 20.0)

        volume = 0.0
        vr = s.volume_ratio
        if vr is not None:
            down_day = s.change_pct is not None and s.change_pct < 0
            if down_day and vr >= 1.5:
                neg.append(f"Heavy selling: {vr:.1f}x average volume on a down day")
            elif vr >= 2.0:
                volume = 15
                pos.append(f"Volume {vr:.1f}x the 20-day average")
            elif vr >= 1.5:
                volume = 11
                pos.append(f"Volume {vr:.1f}x the 20-day average")
            elif vr >= 1.2:
                volume = 7
            elif vr >= 0.8:
                volume = 4
            else:
                neg.append(f"Thin volume ({vr:.1f}x average)")

        relative = 5.0  # neutral when there's no benchmark
        rs = s.relative_strength_20d
        if rs is not None:
            if rs >= 5:
                relative = 10
                pos.append(f"Outperforming NIFTY by {rs:.1f}% over 20 days")
            elif rs >= 0:
                relative = 7
            elif rs >= -5:
                relative = 3
            else:
                relative = 0
                neg.append(f"Underperforming NIFTY by {abs(rs):.1f}% over 20 days")

        penalty = 0.0
        if s.rsi is not None and s.rsi > 75:
            penalty += 8
            neg.append(f"RSI {s.rsi:.0f}: overbought, pullback risk")
        if s.ema_20 and close > s.ema_20 * 1.08:
            penalty += 8
            neg.append(f"Extended {((close / s.ema_20) - 1) * 100:.1f}% above EMA 20 (chasing risk)")
        if s.atr_pct is not None and s.atr_pct > 5:
            penalty += 5
            neg.append(f"Very volatile (ATR {s.atr_pct:.1f}% of price)")
        if s.ema_50 is not None and s.ema_200 is not None and close < s.ema_50 and close < s.ema_200:
            penalty += 5

        total = trend + momentum + structure + volume + relative - penalty
        s.technical_score = round(max(0.0, min(100.0, total)), 2)
        s.signal = self._signal_from_score(s.technical_score)
        s.score_breakdown = {
            "trend": trend, "momentum": momentum, "structure": structure,
            "volume": volume, "relative_strength": relative, "penalty": -penalty,
        }

    @staticmethod
    def _signal_from_score(score: float) -> str:
        if score >= 75:
            return "strong_bullish"
        if score >= 60:
            return "bullish"
        if score >= 45:
            return "neutral"
        if score >= 30:
            return "bearish"
        return "strong_bearish"

    # ------------------------------------------------------------------
    # Historical back-test of each setup on this stock's own history
    # ------------------------------------------------------------------
    @staticmethod
    def backtest_setups(df: pd.DataFrame, flags: pd.DataFrame) -> dict[str, SetupStats]:
        """
        For every past bar where a setup fired, enter at that close with a
        1.5-ATR stop and a 3-ATR target, and hold up to 10 bars. A bar that
        touches both counts as a loss (conservative). The live bar is excluded,
        and signals within 5 bars of the previous one are skipped as repeats.
        """
        highs, lows, closes = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
        atrs = df["atr"].to_numpy()
        n = len(df)
        stats: dict[str, SetupStats] = {}

        for name in PRICE_SETUPS:
            signal_idx = np.flatnonzero(flags[name].to_numpy()[: n - 1])
            wins = losses = 0
            returns: list[float] = []
            last_taken = -BACKTEST_COOLDOWN_BARS - 1
            for i in signal_idx:
                if i - last_taken <= BACKTEST_COOLDOWN_BARS or np.isnan(atrs[i]):
                    continue
                last_taken = i
                entry = closes[i]
                stop = entry - BACKTEST_STOP_ATR * atrs[i]
                target = entry + BACKTEST_TARGET_ATR * atrs[i]
                exit_price = None
                end = min(n, i + 1 + BACKTEST_MAX_BARS)
                for j in range(i + 1, end):
                    if lows[j] <= stop:
                        exit_price, losses = stop, losses + 1
                        break
                    if highs[j] >= target:
                        exit_price, wins = target, wins + 1
                        break
                if exit_price is None:
                    if end - (i + 1) < BACKTEST_MAX_BARS:
                        continue  # trade still open at the end of the data
                    exit_price = closes[end - 1]
                    if exit_price > entry:
                        wins += 1
                    else:
                        losses += 1
                returns.append((exit_price / entry - 1) * 100)

            count = len(returns)
            stats[name] = SetupStats(
                strategy=name,
                signals=count,
                wins=wins,
                losses=losses,
                win_rate=round(wins / count * 100, 1) if count else None,
                avg_return_pct=round(float(np.mean(returns)), 2) if count else None,
            )
        return stats

    # ------------------------------------------------------------------
    # Trade plan
    # ------------------------------------------------------------------
    def build_trade_plan(self, s: TechnicalSnapshot, strategy: str,
                         conviction: float, market_regime: str = "neutral") -> Optional[TradePlan]:
        """
        Entry zone around the current price (shaped by the setup), stop below
        the 10-day swing low but never more than 1.5 ATR from the entry
        midpoint, and a target at a fixed R-multiple per strategy. Long-only: NSE cash delivery can't be
        held short overnight, so bearish setups produce AVOID, not SELL.
        """
        if not s.has_data:
            return None
        close = s.close
        atr = s.atr or close * 0.02

        if strategy == PULLBACK and s.ema_20:
            entry_low, entry_high = max(s.ema_20, close - 0.5 * atr), close + 0.1 * atr
        elif strategy == BREAKOUT and s.resistance and close - s.resistance < atr:
            entry_low, entry_high = s.resistance, close + 0.25 * atr
        else:
            entry_low, entry_high = close - 0.4 * atr, close + 0.2 * atr
        entry_low, entry_high = round_tick(min(entry_low, entry_high)), round_tick(max(entry_low, entry_high))
        entry_mid = (entry_low + entry_high) / 2

        stop = entry_mid - PLAN_MAX_STOP_ATR * atr
        if s.swing_low_10 is not None:
            stop = max(stop, s.swing_low_10 - 0.2 * atr)
        stop = min(stop, entry_low - 0.8 * atr)
        stop = round_tick(stop)

        reward_multiple, holding = STRATEGY_PROFILE.get(strategy, STRATEGY_PROFILE[NO_SETUP])
        risk = entry_mid - stop
        target = round_tick(entry_mid + reward_multiple * risk)
        risk_reward = round((target - entry_mid) / risk, 2) if risk > 0 else 0.0

        if s.trend == "downtrend" or s.technical_score < 40:
            action = "AVOID"
        elif strategy != NO_SETUP and s.technical_score >= 55 and conviction >= 55:
            action = "BUY"
        else:
            action = "WAIT"

        levels = ["low", "medium", "high"]
        level = 1
        if (s.atr_pct or 0) < 2.0 and conviction >= 70:
            level = 0
        if (s.atr_pct or 0) > 3.5 or conviction < 55:
            level = 2
        if market_regime == "risk-off":
            level = min(2, level + 1)

        invalidation = f"A daily close below {stop:.2f} invalidates the setup."
        if strategy == BREAKOUT and s.resistance:
            invalidation = (f"Falling back below the breakout level {s.resistance:.2f} weakens it; "
                            f"a close below {stop:.2f} invalidates it.")
        elif strategy == PULLBACK and s.ema_20:
            invalidation = f"A close below EMA 20 ({s.ema_20:.2f}) and then {stop:.2f} invalidates it."

        exit_logic = (
            f"Book at {target:.2f} ({risk_reward:.1f}R). After a 1R gain ({entry_mid + risk:.2f}), "
            f"trail the stop to entry. Exit at the end of the {holding} window if neither level is hit."
        )

        return TradePlan(
            action=action, instrument="Cash Stock (NSE)", strategy=strategy,
            entry_low=entry_low, entry_high=entry_high, stop_loss=stop, target=target,
            risk_reward=risk_reward, holding_period=holding, risk_level=levels[level],
            invalidation=invalidation, exit_logic=exit_logic,
        )

    @staticmethod
    def _empty_snapshot(symbol: str) -> TechnicalSnapshot:
        return TechnicalSnapshot(
            symbol=symbol, rsi=None, macd=None, macd_signal=None, ema_20=None, ema_50=None,
            volume=None, avg_volume_20=None, volume_ratio=None, support=None, resistance=None,
            volume_spike=False, technical_score=50.0, signal="neutral",
        )

    # ------------------------------------------------------------------
    # Chart overlay series
    # ------------------------------------------------------------------
    def chart_overlays(self, candles: pd.DataFrame) -> dict[str, list]:
        """EMA 20/50, RSI and MACD series aligned to the candles, for the chart."""
        if candles is None or len(candles) < 30:
            return {"ema_20": [], "ema_50": [], "rsi": [], "macd": [], "macd_signal": [], "macd_hist": []}
        df = self.indicator_frame(candles)
        times = [pd.Timestamp(t).isoformat() for t in df["date"]] if "date" in df.columns else list(range(len(df)))

        def series(col: str) -> list[dict]:
            return [{"time": t, "value": round(float(v), 4)}
                    for t, v in zip(times, df[col]) if v is not None and not pd.isna(v)]

        return {col: series(col) for col in ("ema_20", "ema_50", "rsi", "macd", "macd_signal", "macd_hist")}


analysis_service = AnalysisService()
