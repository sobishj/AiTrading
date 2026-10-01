"""
AI analyst learning loop: how the local Qwen model gets better day by day
without retraining its weights.

1. News reads: every new headline about a tracked stock is scored by Qwen
   (impact -2..+2 plus a reason). Recent reads replace keyword sentiment.
2. Forecasts: each weekday morning Qwen forecasts the next HORIZON_DAYS trading
   days for the top-ranked stocks and the user's Manual list, given the
   deterministic facts, its own past forecasts on the stock, its track record
   and its current lessons.
3. Grading: once the horizon has passed, every forecast is scored against the
   real closing price (direction hit and Brier score for the probability).
4. Reflection: after grading, Qwen reviews what it got right and wrong and
   rewrites its lessons (versioned). The lessons go into every later forecast,
   analysis note and chat, so what it learned carries forward.
5. Earned trust: Qwen's forecast moves the conviction score only once it has
   MIN_GRADED_FOR_TRUST graded forecasts and beats the naive "always up/down"
   baseline. The weight grows with its measured edge (capped at ±5 points)
   and falls back to zero if the edge disappears.

6. Practice (market closed): Qwen replays historical charts — a random stock
   at a random past date, seeing only the bars up to that day with the date
   hidden — forecasts, and is graded at once against what really happened.
   Hundreds of graded cases per evening/weekend feed its lessons quickly.
   Practice is tracked separately and never earns ranking weight (it has no
   news and could be subtly optimistic); only live forecasts do.
7. Next-session outlook: in the evening and pre-market, Qwen reads every
   headline since the close plus global cues and FII/DII flows, forecasts the
   next NIFTY session and names the sectors the news moves. Graded next day.

Every forecast keeps its exact prompt and raw answer, so the graded history
doubles as a fine-tuning dataset (training_examples) for later.
"""
import json
import random
import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy.orm import Session

from config import settings
from data_provider import completed_daily_bars, reference_price
from llm_service import llm_service, ungrounded_numbers
from market_service import market_service
from models import AILesson, AIMarketOutlook, AINewsInsight, AIPrediction, AppSettings, Stock
from prompts.prompt_library import PromptLibrary
from utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

HORIZON_DAYS = 5
FLAT_BAND_PCT = 0.5            # |return| below this counts as "flat"
TOP_N_DAILY = 10               # top-ranked stocks forecast each day (plus the Manual list)
MIN_GRADED_FOR_TRUST = 30      # forecasts graded before Qwen may influence ranking
# No short-horizon market call deserves certainty; extreme answers from a small
# model are clamped so one overconfident answer can't dominate the Brier score.
PROBABILITY_FLOOR, PROBABILITY_CEILING = 10.0, 90.0
MAX_AI_ADJUSTMENT = 5.0        # conviction points at full trust
TRUST_SCALE = 50.0             # weight = edge (fraction) * TRUST_SCALE, capped
# One headline per call: when a small model reads several at once it mixes them up.
NEWS_BATCH_SIZE = 1
# Price-tracker pages ("... Share Price Live Updates") carry no news; skip them without asking the model.
PRICE_BLOG_RE = re.compile(
    r"share price (live|today)|stock price (live|today)|price live updates|live updates:|price movement today",
    re.IGNORECASE,
)
NEWS_LOOKBACK_HOURS = 72
MIN_NEW_GRADES_FOR_REFLECTION = 3        # new live grades that trigger a review
PRACTICE_GRADES_PER_REFLECTION = 100     # ...or this many new practice grades (a free local model can practise
                                         # hundreds a night; each review is one call to the background model)
MAX_LESSONS = 8
REFLECTION_LIVE_CASES = 12               # latest graded live forecasts reviewed (all models)
REFLECTION_PRACTICE_CASES = 12
# Conventional indicator settings a lesson may quote without them appearing in the graded record.
STANDARD_THRESHOLDS = "RSI 20 30 40 50 60 70 80; EMA 20 50 200; MACD 12 26 9; 52-week; volume 1.5x 2x; ATR 14"

# Practice on historical charts
PRACTICE_HISTORY_DAYS = 1100             # ~3 years of daily bars to sample from
PRACTICE_MIN_BARS = 220                  # enough history for EMA-200 / 52-week range at the sampled date
PRACTICE_BATCH = 3                       # cases per queued job (keeps the LLM queue responsive)
PRACTICE_MAX_PER_DAY = 400
INTERACTIVE_COOLDOWN_SECONDS = 120       # pause practice this long after the user chats

# Next-session outlook
OUTLOOK_HEADLINES = 25


def background_model_name() -> str:
    """The model that actually serves background work right now (it can be switched at runtime)."""
    profile = llm_service.active("background")
    return profile.model if profile else settings.LLM_MODEL


def practice_model_name() -> str:
    """The model doing chart practice (Settings -> AI models; the background model unless chosen)."""
    profile = llm_service.active("practice")
    return profile.model if profile else background_model_name()


def today_ist() -> date:
    return datetime.now(IST).date()


def headline_key(symbol: str, title: str) -> str:
    return hashlib.sha1(f"{symbol}|{title.strip().lower()}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Parsers (small models drift from formats; be forgiving, never guess)
# ---------------------------------------------------------------------------
_NEWS_LINE_RE = re.compile(r"^\s*(\d+)\s*[|.:)-]\s*([+-]?\d)\s*[|:-]?\s*(.*)$")


def parse_news_lines(text: str, count: int) -> dict[int, tuple[int, str]]:
    """'<n> | <impact> | <reason>' lines -> {n: (impact clamped to -2..2, reason)}."""
    results: dict[int, tuple[int, str]] = {}
    for line in (text or "").splitlines():
        match = _NEWS_LINE_RE.match(line.strip().strip("*"))
        if not match:
            continue
        n, impact = int(match.group(1)), int(match.group(2))
        if 1 <= n <= count and n not in results:
            results[n] = (max(-2, min(2, impact)), match.group(3).strip(" |-")[:200])
    return results


def parse_single_news(text: str) -> Optional[tuple[int, str]]:
    """
    One-headline answer. The model writes either "<impact> | <label> | <reason>"
    or keeps the item number ("1 | <impact> | <reason>"); take the impact, and
    the last segment as the reason.
    """
    line = next((l for l in (text or "").splitlines() if re.search(r"[+-]?\d", l)), "")
    parts = [p.strip(" *") for p in line.split("|")]
    numbers = [int(m.group()) for p in parts[:2] if (m := re.fullmatch(r"[+-]?\d", p))]
    if not numbers:
        return None
    impact = numbers[1] if len(numbers) == 2 and numbers[0] == 1 and not parts[0].startswith(("+", "-")) else numbers[0]
    reason = parts[-1] if len(parts) > 1 else ""
    return max(-2, min(2, impact)), reason[:200]


def parse_prediction(text: str) -> Optional[dict]:
    """DIRECTION / PROBABILITY_UP / EXPECTED_MOVE / REASON lines -> dict, or None if unusable."""
    fields: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = re.match(r"^\W*(DIRECTION|PROBABILITY[_ ]UP|EXPECTED[_ ]MOVE|REASON)[\s*_]*[:=][\s*]*(.+)$",
                         line.strip(), re.IGNORECASE)
        if match:
            fields[match.group(1).upper().replace(" ", "_")] = match.group(2).strip()

    prob_match = re.search(r"-?\d+(\.\d+)?", fields.get("PROBABILITY_UP", ""))
    if not prob_match:
        return None
    probability = max(0.0, min(100.0, float(prob_match.group())))
    if probability <= 1.0 and "." in prob_match.group():
        probability *= 100  # model answered 0.65 instead of 65
    probability = max(PROBABILITY_FLOOR, min(PROBABILITY_CEILING, probability))

    direction_text = fields.get("DIRECTION", "").lower()
    if "up" in direction_text or "bull" in direction_text or "higher" in direction_text:
        direction = "up"
    elif "down" in direction_text or "bear" in direction_text or "lower" in direction_text:
        direction = "down"
    elif "flat" in direction_text or "side" in direction_text:
        direction = "flat"
    else:
        direction = "up" if probability > 55 else "down" if probability < 45 else "flat"

    move_match = re.search(r"[+-]?\d+(\.\d+)?", fields.get("EXPECTED_MOVE", ""))
    move = float(move_match.group()) if move_match else None
    if move is not None:
        move = max(-30.0, min(30.0, move))
    return {"direction": direction, "probability_up": round(probability, 1),
            "expected_move_pct": move, "reason": fields.get("REASON", "")[:400]}


def parse_lessons(text: str) -> list[str]:
    lessons = []
    for line in (text or "").splitlines():
        match = re.match(r"^\s*(?:\d+[.)]|[-*•])\s+(.*\S)\s*$", line)
        if match and len(match.group(1)) > 15:
            lessons.append(match.group(1).replace("**", "").replace("__", "").strip()[:300])
    return lessons[:MAX_LESSONS]


def grounded_lessons(lessons: list[str], source: str) -> tuple[list[str], list[str]]:
    """
    (kept, dropped): a lesson quoting a number that isn't in the graded record
    it was written from (an invented hit rate, price or threshold) is dropped,
    because every model reads these lessons as if they were measured.
    """
    kept, dropped = [], []
    for lesson in lessons:
        (dropped if ungrounded_numbers(lesson, source) else kept).append(lesson)
    return kept, dropped


def trade_outcome(entry: float, bars: pd.DataFrame, target: Optional[float], stop: Optional[float],
                  recommendation: Optional[str]) -> dict:
    """
    Measured from real bars only: max favourable / adverse excursion (long view), whether the
    target or stop traded, and the P&L of following the call (BUY = long, exit at stop, target or
    the horizon close; SELL/AVOID = the move avoided). When a bar spans both levels the stop is
    assumed first (conservative). Unknowns stay None.
    """
    highs, lows = bars["high"].astype(float), bars["low"].astype(float)
    # float(): numpy scalars can't be written to the database (psycopg2 renders them as "np.float64(..)").
    out = {"max_favorable_pct": round(float(highs.max()) / entry * 100 - 100, 2),
           "max_adverse_pct": round(float(lows.min()) / entry * 100 - 100, 2),
           "target_hit": None, "stop_hit": None, "pnl_pct": None}
    rec = (recommendation or "").upper()
    close = float(bars.iloc[-1]["close"])
    if rec == "BUY":
        if target is not None:
            out["target_hit"] = bool((highs >= target).any())
        if stop is not None:
            out["stop_hit"] = bool((lows <= stop).any())
        exit_price = close
        for h, l in zip(highs, lows):
            if stop is not None and l <= stop:
                exit_price = stop
                break
            if target is not None and h >= target:
                exit_price = target
                break
        out["pnl_pct"] = round((exit_price / entry - 1) * 100, 2)
    elif rec in ("SELL", "AVOID"):
        out["pnl_pct"] = round(-(close / entry - 1) * 100, 2)
    return out


PRICE_DOUBT_PCT = 2.0     # feed close vs NSE's official close on the grading day


def price_disagreement(db: Session, symbol: str, bar_date, feed_close: float) -> Optional[str]:
    """Why a grading price can't be trusted (it disagrees with NSE's official close that day), or None."""
    from models import NSEDaily

    day = pd.Timestamp(bar_date)
    day = (day.tz_convert("Asia/Kolkata") if day.tzinfo else day).date()
    row = (db.query(NSEDaily.close).filter(NSEDaily.symbol == symbol, NSEDaily.trade_date == day,
                                            NSEDaily.series.in_(("EQ", "BE"))).first())
    if row is None or not row[0]:
        return None                       # no official record to compare with: grade as before
    official = float(row[0])
    if abs(feed_close / official - 1) * 100 <= PRICE_DOUBT_PCT:
        return None
    return (f"price feed close {feed_close:.2f} on {day} disagreed with NSE's official close {official:.2f} "
            f"by more than {PRICE_DOUBT_PCT:g}%")


def apply_outcome(prediction, bars: pd.DataFrame, entry: Optional[float] = None, scale: float = 1.0) -> None:
    """`entry`/`scale` put the stored prices on the bars' scale after a corporate action (see reference_price)."""
    if bars.empty or not prediction.price_at_prediction:
        return
    o = trade_outcome(entry if entry is not None else float(prediction.price_at_prediction), bars,
                      float(prediction.target) * scale if prediction.target is not None else None,
                      float(prediction.stop_loss) * scale if prediction.stop_loss is not None else None,
                      prediction.recommendation)
    prediction.max_favorable_pct, prediction.max_adverse_pct = o["max_favorable_pct"], o["max_adverse_pct"]
    prediction.target_hit, prediction.stop_hit, prediction.outcome_pnl_pct = o["target_hit"], o["stop_hit"], o["pnl_pct"]


def classify_return(return_pct: float) -> str:
    if return_pct > FLAT_BAND_PCT:
        return "up"
    if return_pct < -FLAT_BAND_PCT:
        return "down"
    return "flat"


def is_correct(predicted: str, return_pct: float) -> bool:
    """Up/down are judged on the sign of the move; flat must stay inside the flat band."""
    if predicted == "up":
        return return_pct > 0
    if predicted == "down":
        return return_pct < 0
    return abs(return_pct) <= FLAT_BAND_PCT


class AIAnalystService:
    # ------------------------------------------------------------------
    # 1. News reads
    # ------------------------------------------------------------------
    async def analyze_news(self, db: Session, items: Iterable[tuple[Stock, dict]]) -> int:
        """Score (stock, headline) pairs not seen before, in small batches. Returns how many were stored."""
        pending: list[tuple[Stock, dict, str]] = []
        seen: set[str] = set()
        for stock, item in items:
            key = headline_key(stock.symbol, item["title"])
            if key in seen or PRICE_BLOG_RE.search(item["title"]):
                continue
            seen.add(key)
            if db.query(AINewsInsight.id).filter(AINewsInsight.headline_key == key).first() is None:
                pending.append((stock, item, key))

        stored = 0
        for start in range(0, len(pending), NEWS_BATCH_SIZE):
            batch = pending[start:start + NEWS_BATCH_SIZE]
            listing = "\n".join(f"{i}. [{stock.name}] {item['title']}" for i, (stock, item, _) in enumerate(batch, 1))
            text = await llm_service.complete(PromptLibrary.news_impact(listing), temperature=0.1, max_tokens=300)
            if text is None:
                break  # model offline: try again on the next scan
            if len(batch) == 1:
                single = parse_single_news(text)
                parsed = {1: single} if single else {}
            else:
                parsed = parse_news_lines(text, len(batch))
            if not parsed:
                logger.debug("Unparseable news read: %r", text[:200])
            for i, (stock, item, key) in enumerate(batch, 1):
                if i not in parsed:
                    continue
                impact, reason = parsed[i]
                published = None
                if item.get("published"):
                    try:
                        published = datetime.fromisoformat(item["published"]).astimezone(timezone.utc).replace(tzinfo=None)
                    except ValueError:
                        published = None
                db.add(AINewsInsight(stock_id=stock.id, headline_key=key, title=item["title"],
                                     link=item.get("link"), published=published, impact=impact,
                                     reason=reason, model=background_model_name(),
                                     source=(item.get("source") or None) and str(item["source"])[:160]))
                stored += 1
            db.commit()
        if stored:
            logger.info("AI analyst read %d new headline(s)", stored)
        return stored

    def match_news(self, stocks: list[Stock], news: list[dict], max_age_hours: int = 24) -> list[tuple[Stock, dict]]:
        """Every (stock, headline) pair where a recent headline mentions a tracked stock."""
        from market_service import keyword_pattern  # local: keep module import surface small

        cutoff = datetime.now(IST) - timedelta(hours=max_age_hours)
        recent = [n for n in news if not n.get("published") or datetime.fromisoformat(n["published"]) >= cutoff]
        pairs = []
        for stock in stocks:
            terms = [k for k in (stock.keywords or stock.name).split(",") if k.strip()]
            pattern = keyword_pattern(terms + ([stock.symbol] if len(stock.symbol) > 3 else []))
            if pattern is None:
                continue
            for item in recent:
                if pattern.search(item["title"] + " " + item.get("summary", "")):
                    pairs.append((stock, item))
        # NSE filings belong to exactly one share: matched by symbol, not by keywords.
        from nse_service import nse_service
        for stock in stocks:
            for item in nse_service.filings_for(stock.symbol):
                if not item.get("published") or datetime.fromisoformat(item["published"]) >= cutoff:
                    pairs.append((stock, item))
        return pairs

    def news_sentiment_map(self, db: Session) -> dict[str, dict]:
        """Per-symbol AI news sentiment from reads in the last NEWS_LOOKBACK_HOURS (0-100 scale)."""
        cutoff = datetime.utcnow() - timedelta(hours=NEWS_LOOKBACK_HOURS)
        rows = (
            db.query(AINewsInsight, Stock.symbol).join(Stock, AINewsInsight.stock_id == Stock.id)
            .filter(AINewsInsight.created_at >= cutoff)
            .order_by(AINewsInsight.created_at.desc()).all()
        )
        grouped: dict[str, list[AINewsInsight]] = defaultdict(list)
        for insight, symbol in rows:
            grouped[symbol].append(insight)
        result = {}
        for symbol, insights in grouped.items():
            relevant = [i for i in insights if i.impact != 0] or insights
            avg = sum(i.impact for i in relevant) / len(relevant)
            score = max(15.0, min(85.0, 50.0 + 15.0 * avg))
            result[symbol] = {
                "score": round(score, 1),
                "sentiment": "positive" if score > 55 else "negative" if score < 45 else "neutral",
                "reads": [{"title": i.title, "impact": i.impact, "reason": i.reason, "link": i.link}
                          for i in insights[:5]],
            }
        return result

    # ------------------------------------------------------------------
    # 2. Forecasts
    # ------------------------------------------------------------------
    def forecast_targets(self, db: Session, ranked: list) -> list:
        """Top-ranked stocks plus the Manual list and open holdings, de-duplicated, in ranking order."""
        from models import Position
        manual = {s.symbol for s in db.query(Stock).filter(Stock.in_manual_list.is_(True)).all()}
        manual |= {p.stock.symbol for p in db.query(Position).filter(Position.status == "open").all()}
        chosen = []
        for item in ranked:
            if (len(chosen) < TOP_N_DAILY or item.symbol in manual) and item.technical.has_data:
                chosen.append(item)
        return chosen

    async def make_daily_predictions(self, db: Session, ranked: list, force: bool = False) -> int:
        from ai_orchestrator import ai_orchestrator

        multi = ai_orchestrator.mode(db) == "multi"
        made = 0
        for item in self.forecast_targets(db, ranked):
            if multi:
                # Every enabled model analyses independently; the evidence-weighted consensus becomes
                # the day's primary forecast (graded, trusted and used exactly like the single-model one).
                exists = (db.query(AIPrediction.id)
                          .filter(AIPrediction.stock_id == item.stock_id, AIPrediction.prediction_date == today_ist(),
                                  AIPrediction.kind == "live", AIPrediction.role == "primary").first())
                if exists and not force:
                    continue
                try:
                    result = await ai_orchestrator.analyze(db, item, trigger="daily", force=True)
                except Exception as exc:  # noqa: BLE001  (one stock's failure must not stop the batch)
                    logger.error("Multi-model forecast for %s failed: %s", item.symbol, exc)
                    db.rollback()
                    continue
                if result and result.get("available"):
                    made += 1
            elif await self.predict_stock(db, item, force=force) is not None:
                made += 1
        if made:
            logger.info("AI analyst made %d forecast(s) for %s", made, today_ist())
        return made

    async def predict_stock(self, db: Session, item, force: bool = False) -> Optional[AIPrediction]:
        today = today_ist()
        existing = (db.query(AIPrediction)
                    .filter(AIPrediction.stock_id == item.stock_id, AIPrediction.prediction_date == today,
                            AIPrediction.kind == "live", AIPrediction.role == "primary").first())
        if existing is not None and not force:
            return None

        prompt = self._prediction_prompt(db, item)
        text = await llm_service.complete(prompt, temperature=0.2, max_tokens=220)
        if text is None:
            return None
        parsed = parse_prediction(text)
        if parsed is None:
            logger.warning("Unparseable AI forecast for %s: %r", item.symbol, text[:200])
            return None

        version, _ = self.current_lessons(db)
        prediction = existing or AIPrediction(stock_id=item.stock_id, prediction_date=today, kind="live",
                                              role="primary")
        prediction.horizon_days = HORIZON_DAYS
        prediction.direction = parsed["direction"]
        prediction.probability_up = parsed["probability_up"]
        prediction.expected_move_pct = parsed["expected_move_pct"]
        prediction.reason = parsed["reason"]
        prediction.price_at_prediction = item.technical.close
        prediction.strategy = item.strategy
        prediction.conviction_at_prediction = item.conviction_score
        prediction.lessons_version = version
        prediction.model = background_model_name()
        bg = llm_service.active("background")
        prediction.profile_id = bg.id if bg else None
        prediction.prompt_text = prompt
        prediction.raw_response = text
        prediction.created_at = datetime.utcnow()
        if existing is None:
            db.add(prediction)
        db.commit()
        db.refresh(prediction)
        return prediction

    def _prediction_prompt(self, db: Session, item) -> str:
        from insight_service import InsightService  # local import: insight imports ranking
        from ranking_service import ranking_service

        plan = item.plan
        call = (f"{plan.action} ({plan.strategy}), conviction {item.conviction_score:.0f}/100, entry "
                f"{plan.entry_low:.2f}-{plan.entry_high:.2f}, stop {plan.stop_loss:.2f}, target {plan.target:.2f}"
                if plan else "no plan")
        reads = item.sentiment.get("ai_reads") or []
        news = ("; ".join(f"{r['title']} (impact {r['impact']:+d}: {r['reason']})" for r in reads[:3])
                or "; ".join(item.sentiment.get("matched_headlines", [])[:3]) or "no stock-specific news")
        _, lessons = self.current_lessons(db)
        return PromptLibrary.ai_prediction(
            name=item.name, symbol=item.symbol, horizon=HORIZON_DAYS,
            market=ranking_service.context.market_summary or "n/a",
            facts="; ".join(InsightService._plain_facts(item)),
            call=call, news=news,
            own_history=self.own_history_text(db, item.stock_id),
            track_record=self.track_record_text(db),
            lessons=self.lessons_block(lessons),
        )

    @staticmethod
    def lessons_block(lessons: list[str]) -> str:
        return "\n".join(f"{i}. {l}" for i, l in enumerate(lessons, 1)) or "None yet — no graded forecasts reviewed so far."

    def own_history_text(self, db: Session, stock_id: int, limit: int = 5) -> str:
        rows = (db.query(AIPrediction)
                .filter(AIPrediction.stock_id == stock_id, AIPrediction.graded_at.isnot(None),
                        AIPrediction.kind == "live", AIPrediction.role == "primary")
                .order_by(AIPrediction.prediction_date.desc()).limit(limit).all())
        if not rows:
            return "none graded yet"
        return "; ".join(
            f"{p.prediction_date:%d %b}: said {p.direction} ({float(p.probability_up):.0f}% up) → "
            f"actual {float(p.actual_return_pct):+.1f}% ({'right' if p.correct else 'wrong'})"
            for p in rows)

    def todays_predictions(self, db: Session) -> dict[str, AIPrediction]:
        """Latest forecast per symbol made within the last 3 days (so weekends keep Friday's view)."""
        cutoff = today_ist() - timedelta(days=3)
        rows = (db.query(AIPrediction, Stock.symbol).join(Stock, AIPrediction.stock_id == Stock.id)
                .filter(AIPrediction.prediction_date >= cutoff, AIPrediction.kind == "live", AIPrediction.role == "primary")
                .order_by(AIPrediction.prediction_date.asc(), AIPrediction.id.asc()).all())
        return {symbol: prediction for prediction, symbol in rows}

    # ------------------------------------------------------------------
    # 3. Grading
    # ------------------------------------------------------------------
    async def grade_predictions(self, db: Session) -> int:
        cutoff = today_ist() - timedelta(days=HORIZON_DAYS)  # calendar pre-filter; bars decide
        pending = (db.query(AIPrediction)
                   .filter(AIPrediction.graded_at.is_(None), AIPrediction.quarantined.is_(None),
                           AIPrediction.prediction_date <= cutoff, AIPrediction.kind == "live").all())
        graded = 0
        for prediction in pending:
            stock = db.get(Stock, prediction.stock_id)
            if stock is None:
                continue
            days = (today_ist() - prediction.prediction_date).days + 10
            candles = completed_daily_bars(await market_service.get_candles_for_symbol(stock.symbol, days=days))
            if candles.empty or "date" not in candles.columns:
                continue
            dates = pd.to_datetime(candles["date"])
            if dates.dt.tz is not None:
                dates = dates.dt.tz_convert("Asia/Kolkata")
            after = candles[dates.dt.date > prediction.prediction_date].reset_index(drop=True)
            if len(after) < prediction.horizon_days:
                continue
            entry, scale = reference_price(candles, prediction.prediction_date, float(prediction.price_at_prediction))
            close = float(after.iloc[prediction.horizon_days - 1]["close"])
            doubt = price_disagreement(db, stock.symbol, after.iloc[prediction.horizon_days - 1]["date"], close)
            if doubt:
                prediction.quarantined = doubt          # never graded on prices that can't be trusted
                logger.warning("Forecast %s for %s set aside: %s", prediction.id, stock.symbol, doubt)
                continue
            ret = (close / entry - 1) * 100
            prediction.actual_return_pct = round(ret, 2)
            prediction.actual_direction = classify_return(ret)
            prediction.correct = is_correct(prediction.direction, ret)
            apply_outcome(prediction, after.iloc[: prediction.horizon_days], entry=entry, scale=scale)
            prediction.graded_at = datetime.utcnow()
            graded += 1
        db.commit()
        if graded:
            logger.info("Graded %d AI forecast(s)", graded)
            try:
                from knowledge_service import knowledge_service
                knowledge_service.update_live_stats(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Live pattern statistics not updated: %s", exc)
                db.rollback()
        return graded

    # ------------------------------------------------------------------
    # 4. Reflection -> lessons
    # ------------------------------------------------------------------
    @staticmethod
    def _shared_rows_filter():
        """
        Every model's own forecasts (single-model, practice, and each member of a
        multi-model run), so the lessons pool what all models learned. The
        combined consensus rows are left out: they restate the members.
        """
        from sqlalchemy import or_
        return or_(AIPrediction.role == "member",
                   (AIPrediction.role == "primary") & (AIPrediction.model != "consensus"))

    def model_records_text(self, db: Session) -> str:
        """Graded live record per source model, e.g. for the reviewer to weigh whose mistakes to learn from."""
        rows = (db.query(AIPrediction.model, AIPrediction.correct)
                .filter(AIPrediction.graded_at.isnot(None), AIPrediction.kind == "live",
                        self._shared_rows_filter()).all())
        by_model: dict[str, list[bool]] = defaultdict(list)
        for model, correct in rows:
            by_model[model or "unknown"].append(bool(correct))
        return "; ".join(f"{m}: {sum(v)}/{len(v)} correct" for m, v in
                         sorted(by_model.items(), key=lambda x: -len(x[1]))) or "no live forecasts graded yet"

    def current_lessons(self, db: Session) -> tuple[int, list[str]]:
        latest = db.query(AILesson).order_by(AILesson.version.desc()).first()
        if latest is None:
            return 0, []
        return latest.version, [l for l in latest.lessons_text.split("\n") if l.strip()]

    async def reflect(self, db: Session, force: bool = False) -> Optional[AILesson]:
        """
        Review graded forecasts from every model and rewrite the shared lessons,
        which then go into every model's prompts — so a newly added model starts
        from what the others already learned instead of from nothing.
        """
        latest = db.query(AILesson).order_by(AILesson.version.desc()).first()
        since = latest.created_at if latest else datetime.min
        own = self._shared_rows_filter()

        def new_since(kind: str) -> int:
            return (db.query(AIPrediction)
                    .filter(AIPrediction.graded_at.isnot(None), AIPrediction.graded_at > since,
                            AIPrediction.kind == kind, own).count())

        if (not force and new_since("live") < MIN_NEW_GRADES_FOR_REFLECTION
                and new_since("practice") < PRACTICE_GRADES_PER_REFLECTION):
            return None

        def latest_graded(kind: str, limit: int) -> list:
            return (db.query(AIPrediction, Stock).join(Stock, AIPrediction.stock_id == Stock.id)
                    .filter(AIPrediction.graded_at.isnot(None), AIPrediction.kind == kind, own)
                    .order_by(AIPrediction.graded_at.desc()).limit(limit).all())

        recent = latest_graded("live", REFLECTION_LIVE_CASES) + latest_graded("practice", REFLECTION_PRACTICE_CASES)
        if not recent:
            return None

        def describe(p: AIPrediction, stock: Stock) -> str:
            when = "practice, date hidden" if p.kind == "practice" else f"{p.prediction_date:%d %b}"
            return (f"- [{p.model or 'unknown model'}] {stock.name} ({when}): predicted {p.direction} "
                    f"({float(p.probability_up):.0f}% up, {p.strategy or 'no setup'}"
                    f"{', regime ' + p.market_regime if p.market_regime else ''}); reason: {p.reason or 'n/a'} → "
                    f"actual {float(p.actual_return_pct):+.1f}% ({'RIGHT' if p.correct else 'WRONG'})")

        graded_text = "\n".join(describe(p, stock) for p, stock in recent)
        track_record = self.track_record_text(db) + "; practice: " + self.track_record_text(db, kind="practice")
        model_records = self.model_records_text(db)
        version, lessons = self.current_lessons(db)
        text = await llm_service.complete(PromptLibrary.ai_reflection(
            current_lessons="\n".join(f"{i}. {l}" for i, l in enumerate(lessons, 1)) or "none yet",
            track_record=track_record, model_records=model_records, graded=graded_text,
        ), temperature=0.3, max_tokens=500)
        parsed = parse_lessons(text or "")
        new_lessons, dropped = grounded_lessons(
            parsed, "\n".join((graded_text, track_record, model_records, STANDARD_THRESHOLDS)))
        if dropped:
            logger.warning("Dropped %d lesson(s) quoting numbers not in the graded record: %s", len(dropped), dropped)
        if len(new_lessons) < 2:
            logger.warning("AI reflection produced no usable lessons: %r", (text or "")[:200])
            return None

        source_models = sorted({p.model for p, _ in recent if p.model})
        lesson = AILesson(version=version + 1, lessons_text="\n".join(new_lessons),
                          based_on_predictions=(db.query(AIPrediction)
                                                .filter(AIPrediction.graded_at.isnot(None), own).count()),
                          hit_rate_at_creation=self._shared_hit_rate(db),
                          model=background_model_name(), source_models=",".join(source_models) or None)
        db.add(lesson)
        db.commit()
        db.refresh(lesson)
        logger.info("Shared lessons v%d written from graded forecasts of %s", lesson.version,
                    ", ".join(source_models) or "no model")
        return lesson

    def _shared_hit_rate(self, db: Session) -> Optional[float]:
        """Live hit rate across every model's forecasts (practice if nothing live is graded yet)."""
        for kind in ("live", "practice"):
            rows = [c for (c,) in db.query(AIPrediction.correct)
                    .filter(AIPrediction.graded_at.isnot(None), AIPrediction.kind == kind,
                            self._shared_rows_filter())]
            if rows:
                return round(sum(1 for c in rows if c) / len(rows) * 100, 1)
        return None

    # ------------------------------------------------------------------
    # 5. Track record and earned trust
    # ------------------------------------------------------------------
    def performance(self, db: Session, kind: str = "live") -> dict:
        """Track record for live forecasts (default; the only kind that earns trust) or practice."""
        graded = (db.query(AIPrediction).filter(AIPrediction.graded_at.isnot(None), AIPrediction.kind == kind,
                                                AIPrediction.role == "primary")
                  .order_by(AIPrediction.graded_at.asc()).all())
        total = db.query(AIPrediction).filter(AIPrediction.kind == kind, AIPrediction.role == "primary",
                                              AIPrediction.quarantined.is_(None)).count()
        n = len(graded)
        result = {"total_forecasts": total, "graded": n, "pending": total - n, "hit_rate": None, "brier": None,
                  "baseline_hit_rate": None, "edge": None, "trust_weight": 0.0, "by_week": [],
                  "by_lessons_version": [], "min_graded_for_trust": MIN_GRADED_FOR_TRUST,
                  "horizon_days": HORIZON_DAYS}
        if n == 0:
            return result

        hits = sum(1 for p in graded if p.correct)
        brier = sum(((float(p.probability_up) / 100) - (1.0 if float(p.actual_return_pct) > 0 else 0.0)) ** 2
                    for p in graded) / n
        ups = sum(1 for p in graded if float(p.actual_return_pct) > 0)
        baseline = max(ups, n - ups) / n  # best of "always up" / "always down" in hindsight
        hit_rate = hits / n
        edge = hit_rate - baseline

        weeks: dict[str, list[bool]] = defaultdict(list)
        versions: dict[int, list[bool]] = defaultdict(list)
        for p in graded:
            # Practice cases carry a hidden historical date; bucket them by when they were graded.
            when = p.prediction_date if kind == "live" else (p.graded_at or p.created_at).date()
            week = (when - timedelta(days=when.weekday())).isoformat()
            weeks[week].append(bool(p.correct))
            versions[p.lessons_version].append(bool(p.correct))

        result.update({
            "hit_rate": round(hit_rate * 100, 1),
            "brier": round(brier, 3),
            "baseline_hit_rate": round(baseline * 100, 1),
            "edge": round(edge * 100, 1),
            "trust_weight": self.trust_weight(n, edge) if kind == "live" else 0.0,
            "by_week": [{"week": w, "forecasts": len(v), "hit_rate": round(sum(v) / len(v) * 100, 1)}
                        for w, v in sorted(weeks.items())],
            "by_lessons_version": [{"version": ver, "forecasts": len(v), "hit_rate": round(sum(v) / len(v) * 100, 1)}
                                   for ver, v in sorted(versions.items())],
        })
        return result

    @staticmethod
    def trust_weight(graded: int, edge: float) -> float:
        """Conviction points Qwen may move a score by: 0 until proven, then proportional to its edge."""
        if graded < MIN_GRADED_FOR_TRUST or edge <= 0:
            return 0.0
        return round(min(MAX_AI_ADJUSTMENT, edge * TRUST_SCALE), 2)

    @staticmethod
    def conviction_adjustment(probability_up: float, weight: float) -> float:
        return round(weight * (probability_up - 50.0) / 50.0, 2)

    def track_record_text(self, db: Session, kind: str = "live") -> str:
        perf = self.performance(db, kind=kind)
        if not perf["graded"]:
            return f"no {'practice ' if kind == 'practice' else ''}forecasts graded yet ({perf['pending']} pending)"
        return (f"{perf['graded']} forecasts graded, {perf['hit_rate']:.0f}% correct vs "
                f"{perf['baseline_hit_rate']:.0f}% for the naive always-up/always-down baseline; "
                f"Brier score {perf['brier']:.3f} (lower is better, 0.25 = coin flip)")

    # ------------------------------------------------------------------
    # Fine-tuning dataset (for a future GPU / LoRA run)
    # ------------------------------------------------------------------
    def training_examples(self, db: Session) -> Iterable[dict]:
        """Graded forecasts as prompt/response pairs with outcome labels."""
        rows = (db.query(AIPrediction, Stock).join(Stock, AIPrediction.stock_id == Stock.id)
                .filter(AIPrediction.graded_at.isnot(None), AIPrediction.prompt_text.isnot(None))
                .order_by(AIPrediction.prediction_date.asc()).all())
        for p, stock in rows:
            ret = float(p.actual_return_pct)
            yield {
                "kind": p.kind,
                "symbol": stock.symbol,
                "date": p.prediction_date.isoformat(),
                "prompt": p.prompt_text,
                "model_response": p.raw_response,
                "correct": bool(p.correct),
                "actual_return_pct": ret,
                # What a well-calibrated answer would have said, for supervised fine-tuning.
                "target_response": (f"DIRECTION: {classify_return(ret)}\nPROBABILITY_UP: {75 if ret > 0 else 25}\n"
                                    f"EXPECTED_MOVE: {ret:.1f}\nREASON: (outcome-labelled example)"),
            }


    # ------------------------------------------------------------------
    # 6. Practice on historical charts (market closed)
    # ------------------------------------------------------------------
    def practice_enabled(self, db: Session) -> bool:
        cfg = db.get(AppSettings, 1)
        return bool(cfg is None or cfg.ai_practice_enabled)

    def practice_done_today(self, db: Session) -> int:
        start = datetime.combine(today_ist(), datetime.min.time()) - timedelta(hours=5, minutes=30)
        return (db.query(AIPrediction)
                .filter(AIPrediction.kind == "practice", AIPrediction.created_at >= start).count())

    async def run_practice_batch(self, db: Session, count: int = PRACTICE_BATCH) -> int:
        """Replay `count` random (stock, past date) cases; each is forecast and graded immediately."""
        from analysis_service import analysis_service

        stocks = db.query(Stock).filter(Stock.watchlist_status == "active").all() or \
            db.query(Stock).filter(Stock.in_manual_list.is_(True)).all()
        if not stocks:
            return 0
        nifty = await market_service.get_candles_for_symbol("^NSEI", days=PRACTICE_HISTORY_DAYS)
        done = 0
        for _ in range(count):
            stock = random.choice(stocks)
            candles = await market_service.get_candles_for_symbol(stock.symbol, days=PRACTICE_HISTORY_DAYS)
            if len(candles) < PRACTICE_MIN_BARS + HORIZON_DAYS + 1:
                continue
            # The sampled day must have HORIZON_DAYS real bars after it; exclude the live bar.
            i = random.randint(PRACTICE_MIN_BARS, len(candles) - HORIZON_DAYS - 2)
            as_of = pd.Timestamp(candles["date"].iloc[i])
            as_of_date = as_of.tz_convert("Asia/Kolkata").date() if as_of.tzinfo else as_of.date()
            if (db.query(AIPrediction.id).filter(AIPrediction.kind == "practice", AIPrediction.stock_id == stock.id,
                                                 AIPrediction.prediction_date == as_of_date).first()):
                continue
            prediction = await self._practice_case(db, stock, candles.iloc[: i + 1], nifty, as_of,
                                                   future_close=float(candles["close"].iloc[i + HORIZON_DAYS]))
            if prediction is not None:
                done += 1
        if done:
            logger.info("AI analyst practised %d historical case(s)", done)
        return done

    async def _practice_case(self, db: Session, stock: Stock, history: pd.DataFrame, nifty: pd.DataFrame,
                             as_of: pd.Timestamp, future_close: float) -> Optional[AIPrediction]:
        from analysis_service import analysis_service
        from insight_service import InsightService

        nifty_dates = pd.to_datetime(nifty["date"]) if not nifty.empty else pd.Series(dtype="datetime64[ns]")
        nifty_hist = nifty[nifty_dates <= as_of] if not nifty.empty else nifty
        benchmark = analysis_service.compute_indicators("NIFTY 50", nifty_hist) if len(nifty_hist) >= 30 else None
        # `now` far in the future: no bar is treated as a partial live session.
        snap = analysis_service.compute_indicators(stock.symbol, history,
                                                   benchmark.return_20d if benchmark else None,
                                                   now=datetime(2100, 1, 1, tzinfo=IST))
        if not snap.has_data:
            return None
        strategy = snap.setups[0] if snap.setups else "No Setup"
        plan = analysis_service.build_trade_plan(snap, strategy, snap.technical_score)
        facts_item = type("Case", (), {"technical": snap})()
        market = (f"historical practice case (date hidden). NIFTY 50 was in a {benchmark.trend}, "
                  f"{benchmark.return_20d:+.1f}% over 20 sessions, RSI {benchmark.rsi:.0f}"
                  if benchmark and benchmark.has_data and benchmark.return_20d is not None and benchmark.rsi is not None
                  else "historical practice case (date hidden); index context unavailable")
        _, lessons = self.current_lessons(db)
        prompt = PromptLibrary.ai_prediction(
            name=stock.name, symbol=stock.symbol, horizon=HORIZON_DAYS, market=market,
            facts="; ".join(InsightService._plain_facts(facts_item)),
            call=(f"{plan.action} ({plan.strategy}), technical score {snap.technical_score:.0f}/100" if plan else "none"),
            news="not available for this historical case — judge from the chart alone",
            own_history="n/a (practice case)",
            track_record=self.track_record_text(db, kind="practice"),
            lessons=self.lessons_block(lessons),
        )
        text = await llm_service.complete(prompt, temperature=0.2, max_tokens=220, role="practice")
        if text is None:
            return None
        parsed = parse_prediction(text)
        if parsed is None:
            return None

        ret = (future_close / snap.close - 1) * 100
        version, _ = self.current_lessons(db)
        as_of_date = as_of.tz_convert("Asia/Kolkata").date() if as_of.tzinfo else as_of.date()
        prediction = AIPrediction(
            stock_id=stock.id, prediction_date=as_of_date, kind="practice", horizon_days=HORIZON_DAYS,
            direction=parsed["direction"], probability_up=parsed["probability_up"],
            expected_move_pct=parsed["expected_move_pct"], reason=parsed["reason"],
            price_at_prediction=snap.close, strategy=strategy, conviction_at_prediction=snap.technical_score,
            lessons_version=version, model=practice_model_name(), prompt_text=prompt, raw_response=text,
            actual_return_pct=round(ret, 2), actual_direction=classify_return(ret),
            correct=is_correct(parsed["direction"], ret), graded_at=datetime.utcnow(),
        )
        db.add(prediction)
        db.commit()
        return prediction

    # ------------------------------------------------------------------
    # 7. Next-session market outlook
    # ------------------------------------------------------------------
    @staticmethod
    def next_session_date(now: Optional[datetime] = None) -> date:
        """The next NSE session: today before the close on a weekday, else the next weekday (holidays ignored)."""
        now = now or datetime.now(IST)
        day = now.date()
        if now.weekday() < 5 and now.time() < datetime.strptime("15:30", "%H:%M").time():
            return day
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    async def make_market_outlook(self, db: Session) -> Optional[AIMarketOutlook]:
        from universe import UNIVERSE

        overview = await market_service.get_market_overview(force_refresh=True)
        news = await market_service.fetch_news_async(force_refresh=True)
        session = self.next_session_date()
        headlines = news[:OUTLOOK_HEADLINES]
        by_name = {i["name"]: i for i in overview["indices"]}
        global_markets = ", ".join(
            f"{n} {by_name[n]['change_pct']:+.2f}%" for n in ("S&P 500", "Nasdaq", "Nikkei 225", "Hang Seng",
                                                                 "Crude Oil", "USD/INR")
            if n in by_name and by_name[n]["change_pct"] is not None) or "unavailable"
        flows = overview["fii_dii"]
        sectors = sorted({sector for _, _, sector, _ in UNIVERSE} |
                         {s.sector for s in db.query(Stock).filter(Stock.sector.isnot(None)).all()})
        _, lessons = self.current_lessons(db)
        prompt = PromptLibrary.market_outlook(
            session_date=f"{session:%A %d %b %Y}",
            last_session=overview["summary"],
            global_markets=global_markets,
            flows=(f"FII net ₹{flows['fii_activity']:,.0f} cr, DII net ₹{flows['dii_activity']:,.0f} cr ({flows.get('date')})"
                   if flows.get("fii_activity") is not None else "unavailable"),
            headlines="\n".join(f"- {h['title']}" for h in headlines) or "none",
            track_record=self.outlook_track_record_text(db),
            lessons=self.lessons_block(lessons),
            sectors=", ".join(sectors),
        )
        text = await llm_service.complete(prompt, temperature=0.2, max_tokens=400)
        if text is None:
            return None
        parsed = parse_prediction(text.replace("BIAS:", "DIRECTION:").replace("Bias:", "DIRECTION:"))
        if parsed is None:
            logger.warning("Unparseable market outlook: %r", text[:200])
            return None
        summary = next((m.group(1).strip() for m in re.finditer(r"^\W*SUMMARY\W*[:=]\s*(.+)$", text,
                                                                  re.IGNORECASE | re.MULTILINE)), "")
        version, _ = self.current_lessons(db)
        outlook = AIMarketOutlook(
            session_date=session, bias=parsed["direction"], probability_up=parsed["probability_up"],
            summary=summary[:600], sector_impacts_json=json.dumps(parse_sector_impacts(text, sectors)),
            headlines_considered=len(headlines), lessons_version=version, model=background_model_name(),
            prompt_text=prompt, raw_response=text,
        )
        db.add(outlook)
        db.commit()
        db.refresh(outlook)
        logger.info("AI market outlook for %s: %s (%.0f%% up)", session, outlook.bias, float(outlook.probability_up))
        return outlook

    def latest_outlook(self, db: Session) -> Optional[AIMarketOutlook]:
        """The newest outlook for the upcoming session, or None: a forecast for a session
        that has already closed is history, not an outlook (see /ai/outlooks)."""
        return (db.query(AIMarketOutlook)
                .filter(AIMarketOutlook.session_date >= self.next_session_date())
                .order_by(AIMarketOutlook.created_at.desc()).first())

    @staticmethod
    def outlook_payload(outlook: Optional[AIMarketOutlook]) -> Optional[dict]:
        if outlook is None:
            return None
        return {
            "id": outlook.id,
            "session_date": outlook.session_date.isoformat(),
            "bias": outlook.bias,
            "probability_up": float(outlook.probability_up),
            "summary": outlook.summary,
            "sector_impacts": json.loads(outlook.sector_impacts_json or "[]"),
            "headlines_considered": outlook.headlines_considered,
            "generated_at": outlook.created_at.isoformat(),
            "actual_return_pct": float(outlook.actual_return_pct) if outlook.actual_return_pct is not None else None,
            "correct": outlook.correct,
        }

    async def grade_outlooks(self, db: Session) -> int:
        pending = (db.query(AIMarketOutlook)
                   .filter(AIMarketOutlook.graded_at.is_(None), AIMarketOutlook.session_date <= today_ist()).all())
        if not pending:
            return 0
        nifty = completed_daily_bars(await market_service.get_candles_for_symbol("^NSEI", days=60))
        if nifty.empty:
            return 0
        dates = pd.to_datetime(nifty["date"])
        if dates.dt.tz is not None:
            dates = dates.dt.tz_convert("Asia/Kolkata")
        day_list = list(dates.dt.date)
        now = datetime.now(IST)
        graded = 0
        for outlook in pending:
            if outlook.session_date not in day_list:
                continue
            if outlook.session_date == now.date() and now.time() < datetime.strptime("15:35", "%H:%M").time():
                continue  # session not finished yet
            idx = day_list.index(outlook.session_date)
            if idx == 0:
                continue
            ret = (float(nifty["close"].iloc[idx]) / float(nifty["close"].iloc[idx - 1]) - 1) * 100
            outlook.actual_return_pct = round(ret, 2)
            outlook.correct = is_correct(outlook.bias, ret)
            outlook.graded_at = datetime.utcnow()
            graded += 1
        db.commit()
        return graded

    def outlook_performance(self, db: Session) -> dict:
        graded = db.query(AIMarketOutlook).filter(AIMarketOutlook.graded_at.isnot(None)).all()
        n = len(graded)
        if n == 0:
            return {"graded": 0, "hit_rate": None, "total": db.query(AIMarketOutlook).count()}
        return {"graded": n, "hit_rate": round(sum(1 for o in graded if o.correct) / n * 100, 1),
                "total": db.query(AIMarketOutlook).count()}

    def outlook_track_record_text(self, db: Session) -> str:
        perf = self.outlook_performance(db)
        if not perf["graded"]:
            return "no next-session calls graded yet"
        return f"{perf['graded']} next-session calls graded, {perf['hit_rate']:.0f}% correct"


def parse_sector_impacts(text: str, allowed: list[str]) -> list[dict]:
    """'SECTOR: <name> | <impact> | <reason>' lines, keeping only sectors from the allowed list."""
    lookup = {a.lower(): a for a in allowed}
    impacts = []
    for match in re.finditer(r"^\W*SECTOR\W*[:=]\s*(.+)$", text or "", re.IGNORECASE | re.MULTILINE):
        parts = [p.strip(" *") for p in match.group(1).split("|")]
        if len(parts) < 2:
            continue
        name = lookup.get(parts[0].lower())
        number = re.search(r"[+-]?\d", parts[1])
        if name is None or number is None or any(i["sector"] == name for i in impacts):
            continue
        impacts.append({"sector": name, "impact": max(-2, min(2, int(number.group()))),
                        "reason": parts[2][:160] if len(parts) > 2 else ""})
    return impacts[:5]


ai_analyst_service = AIAnalystService()
