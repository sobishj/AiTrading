"""Unit tests for holding alerts (pure rules) and LLM key handling — no DB, no network."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_providers import mask_key, resolve_key  # noqa: E402
from position_service import evaluate  # noqa: E402


def tech(**kw):
    base = dict(close=100.0, trend="uptrend", ema_20=98.0, ema_50=95.0, macd=1.0, macd_signal=0.5,
                volume_ratio=1.0, change_pct=0.5, atr_pct=2.0)
    base.update(kw)
    return SimpleNamespace(**base)


def kinds(ev):
    return {a["kind"] for a in ev.alerts}


def test_stop_hit_is_critical_and_reports_loss():
    ev = evaluate(name="BEL", quantity=10, avg_price=100, price=94, stop_loss=95, target=110, technical=tech(close=94))
    stop = next(a for a in ev.alerts if a["kind"] == "stop_hit")
    assert stop["severity"] == "critical"
    assert "−₹60" in stop["message"]
    assert "loss_risk" not in kinds(ev)          # the stop alert already covers it


def test_near_stop_uses_atr_band():
    ev = evaluate(name="X", quantity=1, avg_price=100, price=96, stop_loss=95, target=None, technical=tech(atr_pct=3))
    assert "near_stop" in kinds(ev)             # within max(1%, ATR/2 = 1.5%) of the stop
    ev = evaluate(name="X", quantity=1, avg_price=100, price=99, stop_loss=95, target=None, technical=tech(atr_pct=3))
    assert "near_stop" not in kinds(ev)


def test_target_hit_and_near_target():
    assert "target_hit" in kinds(evaluate(name="X", quantity=5, avg_price=100, price=111, stop_loss=95, target=110))
    assert "near_target" in kinds(evaluate(name="X", quantity=5, avg_price=100, price=109.5, stop_loss=95, target=110))


def test_trailing_stop_suggestion_after_1r():
    ev = evaluate(name="X", quantity=5, avg_price=100, price=106, stop_loss=95, target=120)   # +1.2R
    trail = next(a for a in ev.alerts if a["kind"] == "trail_stop")
    assert "₹100.00" in trail["message"] and "break-even" in trail["message"]
    ev = evaluate(name="X", quantity=5, avg_price=100, price=111, stop_loss=100, target=130)  # stop already at entry, R undefined
    assert "trail_stop" not in kinds(ev)


def test_loss_risk_accumulates_reasons_and_respects_threshold():
    bearish = tech(close=93, trend="downtrend", ema_20=97, ema_50=99, macd=-1, macd_signal=0.2,
                   volume_ratio=2.0, change_pct=-2.5)
    ev = evaluate(name="X", quantity=10, avg_price=100, price=93, stop_loss=85, target=120, technical=bearish,
                  plan_action="AVOID", ai_forecast=(25.0, "down"), ai_news_impacts=[-2], sector_impact=-1,
                  market_regime="risk-off", risk_threshold=60)
    assert ev.loss_risk >= 60
    assert "loss_risk" in kinds(ev)
    assert any("downtrend" in r for r in ev.reasons) and any("AI analyst" in r for r in ev.reasons)
    calm = evaluate(name="X", quantity=10, avg_price=100, price=103, stop_loss=90, target=130, technical=tech(close=103))
    assert calm.loss_risk < 30 and "loss_risk" not in kinds(calm)


def test_keys_are_masked_and_env_refs_resolve(monkeypatch):
    assert mask_key("sk-ant-api03-abcdefghijklmnop") == "sk-ant…mnop"
    assert mask_key("env:ANTHROPIC_API_KEY") == "env:ANTHROPIC_API_KEY"
    monkeypatch.setenv("MY_TEST_KEY", "secret-value")
    assert resolve_key("env:MY_TEST_KEY") == "secret-value"
    assert resolve_key("") is None


def tx(id_, side, qty, price, day="2026-09-01"):
    return SimpleNamespace(id=id_, side=side, quantity=qty, price=price, trade_date=day)


def test_replay_average_cost_and_partial_sells():
    from position_service import replay
    r = replay([tx(1, "BUY", 10, 100), tx(2, "BUY", 10, 110), tx(3, "SELL", 5, 120)])
    assert r.quantity == 15 and r.avg_price == 105
    assert r.sell_pnls[3] == 75.0 and r.realized_pnl == 75.0


def test_replay_rejects_selling_more_than_held():
    import pytest
    from position_service import replay
    with pytest.raises(ValueError):
        replay([tx(1, "BUY", 5, 100), tx(2, "SELL", 6, 120)])


def test_suggestion_sell_on_target_and_stop():
    from position_service import suggest
    base = dict(avg_price=100, plan_stop=None, plan_target=None, atr=2, loss_risk=10, risk_threshold=60, plan_action="BUY")
    assert suggest(price=111, stop_loss=95, target=110, **base)["action"] == "SELL"
    s = suggest(price=94, stop_loss=95, target=110, **base)
    assert s["action"] == "SELL" and "stop-loss" in s["reason"]
    assert suggest(price=103, stop_loss=95, target=110, **base)["action"] == "HOLD"


def test_suggestion_trails_stop_and_never_loosens_it():
    from position_service import suggest
    s = suggest(avg_price=100, price=106, stop_loss=95, target=120, plan_stop=101, plan_target=112,
                atr=2, loss_risk=10, risk_threshold=60, plan_action="BUY")
    assert s["suggested_stop"] >= 100        # up 1.2R -> at least break-even
    assert s["suggested_target"] == 112      # the engine's current target
    s = suggest(avg_price=100, price=104, stop_loss=102, target=None, plan_stop=97, plan_target=110,
                atr=2, loss_risk=10, risk_threshold=60, plan_action="BUY")
    assert s["suggested_stop"] >= 102        # never suggests a looser stop than yours


def test_suggestion_consider_selling_on_high_risk_in_loss():
    from position_service import suggest
    s = suggest(avg_price=100, price=96, stop_loss=90, target=120, plan_stop=None, plan_target=None,
                atr=2, loss_risk=82, risk_threshold=60, plan_action="AVOID")
    assert s["action"] == "CONSIDER_SELLING"
