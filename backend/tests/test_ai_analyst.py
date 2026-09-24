"""Unit tests for the AI analyst's parsing, grading and trust rules (no LLM, no DB)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_analyst_service import (  # noqa: E402
    MAX_AI_ADJUSTMENT, MIN_GRADED_FOR_TRUST, AIAnalystService, classify_return, is_correct,
    parse_lessons, parse_news_lines, parse_prediction,
)


def test_parse_news_lines_tolerates_format_drift():
    text = "1 | +2 | record order win from ministry\n2| -1 |margin pressure\n**3 | 0 | live price blog**\n9 | 1 | out of range\nnoise"
    parsed = parse_news_lines(text, 3)
    assert parsed == {1: (2, "record order win from ministry"), 2: (-1, "margin pressure"), 3: (0, "live price blog")}


def test_parse_news_impact_is_clamped():
    assert parse_news_lines("1 | 5 | huge", 1)[1][0] == 2


def test_parse_prediction_happy_path():
    text = "DIRECTION: up\nPROBABILITY_UP: 64\nEXPECTED_MOVE: 2.5%\nREASON: Uptrend with strong volume."
    assert parse_prediction(text) == {"direction": "up", "probability_up": 64.0, "expected_move_pct": 2.5,
                                      "reason": "Uptrend with strong volume."}


def test_parse_prediction_variants():
    p = parse_prediction("**Direction:** Down\nProbability Up: 0.3\nExpected move: -1.8\nReason: weak")
    assert p["direction"] == "down" and p["probability_up"] == 30.0 and p["expected_move_pct"] == -1.8
    # Direction missing: inferred from probability
    assert parse_prediction("PROBABILITY_UP: 70")["direction"] == "up"
    assert parse_prediction("DIRECTION: down\nPROBABILITY_UP: 0")["probability_up"] == 10.0  # clamped
    # No probability: unusable, never guessed
    assert parse_prediction("DIRECTION: up\nREASON: vibes") is None


def test_parse_lessons_keeps_numbered_specific_lines():
    text = "Here are my lessons:\n1. Trust uptrends above EMA 50 only when volume confirms.\n2) Short.\n- Be less confident in risk-off regimes when VIX is rising."
    assert parse_lessons(text) == ["Trust uptrends above EMA 50 only when volume confirms.",
                                   "Be less confident in risk-off regimes when VIX is rising."]


def test_grading_rules():
    assert classify_return(1.2) == "up" and classify_return(-0.7) == "down" and classify_return(0.3) == "flat"
    assert is_correct("up", 0.2) and not is_correct("up", -0.2)
    assert is_correct("down", -3.0) and is_correct("flat", 0.4) and not is_correct("flat", 1.5)


def test_trust_is_earned_and_capped():
    assert AIAnalystService.trust_weight(MIN_GRADED_FOR_TRUST - 1, 0.20) == 0.0   # not enough evidence
    assert AIAnalystService.trust_weight(100, -0.05) == 0.0                     # worse than baseline
    assert 0 < AIAnalystService.trust_weight(100, 0.04) < MAX_AI_ADJUSTMENT
    assert AIAnalystService.trust_weight(100, 0.50) == MAX_AI_ADJUSTMENT


def test_conviction_adjustment_direction():
    assert AIAnalystService.conviction_adjustment(75, 4.0) == 2.0
    assert AIAnalystService.conviction_adjustment(25, 4.0) == -2.0
    assert AIAnalystService.conviction_adjustment(50, 4.0) == 0.0


def test_price_blog_headlines_are_skipped():
    from ai_analyst_service import PRICE_BLOG_RE
    assert PRICE_BLOG_RE.search("Grasim Inds Share Price Live Updates: Grasim Industries Current Price Update")
    assert PRICE_BLOG_RE.search("Cipla Share Price Today: Cipla's Price Movement Today")
    assert not PRICE_BLOG_RE.search("IRDAI paper impact: Insurance stocks crash - PB Fintech, HDFC Life")


def test_parse_single_news_both_shapes():
    from ai_analyst_service import parse_single_news
    assert parse_single_news("-2 | strongly negative | IRDAI paper impacts insurance stocks negatively.") == (
        -2, "IRDAI paper impacts insurance stocks negatively.")
    assert parse_single_news("1 | +1 | order win") == (1, "order win")
    assert parse_single_news("1 | 0 | no news") == (0, "no news")
    assert parse_single_news("+1 | positive | strong results") == (1, "strong results")
    assert parse_single_news("I cannot judge this") is None


def test_parse_sector_impacts_filters_to_known_sectors():
    from ai_analyst_service import parse_sector_impacts
    text = ("BIAS: down\nPROBABILITY_UP: 35\nSUMMARY: crude spike\n"
            "SECTOR: Aviation | -2 | fuel costs jump\nSECTOR: **Energy** | +1 | better realisations\n"
            "SECTOR: Crypto | 2 | not a listed sector\nSECTOR: Aviation | 1 | duplicate ignored")
    assert parse_sector_impacts(text, ["Aviation", "Energy", "Banking"]) == [
        {"sector": "Aviation", "impact": -2, "reason": "fuel costs jump"},
        {"sector": "Energy", "impact": 1, "reason": "better realisations"},
    ]


def test_next_session_date_rules():
    from datetime import datetime
    from ai_analyst_service import IST, AIAnalystService
    nxt = AIAnalystService.next_session_date
    assert str(nxt(datetime(2026, 9, 24, 8, 0, tzinfo=IST))) == "2026-09-24"   # Thu pre-market -> today
    assert str(nxt(datetime(2026, 9, 24, 20, 0, tzinfo=IST))) == "2026-09-25"  # Thu evening -> Fri
    assert str(nxt(datetime(2026, 9, 25, 20, 0, tzinfo=IST))) == "2026-09-28"  # Fri evening -> Mon
    assert str(nxt(datetime(2026, 9, 26, 12, 0, tzinfo=IST))) == "2026-09-28"  # Saturday -> Mon
