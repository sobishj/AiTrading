"""
Morning Brief (PRD §13): at MORNING_BRIEF_TIME (IST) on weekdays — or on demand
the first time it's requested that day — rank the full watchlist, pick today's
best trade, and store a brief with the market context. A deterministic brief
is saved immediately; the local LLM's prose version is added in the background
when the model is available.
"""
import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from llm_service import llm_service
from market_service import market_service
from models import MorningBrief
from ranking_service import ENGINE_VERSION, ranking_service
from utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> date:
    return datetime.now(IST).date()


class BriefService:
    def get_today(self, db: Session) -> Optional[MorningBrief]:
        return db.query(MorningBrief).filter(MorningBrief.brief_date == today_ist()).first()

    @staticmethod
    def is_current(brief: MorningBrief) -> bool:
        """False when the brief's pick came from older engine logic."""
        return json.loads(brief.context_json or "{}").get("engine_version") == ENGINE_VERSION

    async def generate(self, db: Session, force: bool = False) -> MorningBrief:
        existing = self.get_today(db)
        if existing is not None and not force:
            return existing

        overview = await market_service.get_market_overview(force_refresh=True)
        news = await market_service.fetch_news_async(force_refresh=True)
        ranked = await ranking_service.run_full_ranking(db, force=True)
        await ranking_service.persist_top_recommendations(db, ranked)
        cfg = ranking_service.get_or_create_settings(db)

        best = ranking_service.get_best_pick(ranked)
        best_rec = await ranking_service.generate_recommendation(db, best) if best else None
        others = [r for r in ranked if r.action == "BUY" and (best is None or r.symbol != best.symbol)][:3]

        best_text = self._pick_line(best, cfg) if best else (
            ("NIFTY is below its 50-day EMA, so no new long trades today — historically, long picks made "
             "in a falling market lagged. " if not ranking_service.context.market_uptrend else "")
            + "No stock meets the buy criteria today. Best-ranked names are still in WAIT: "
            + ", ".join(f"{r.name} ({r.conviction_score:.0f})" for r in ranked[:3]) + "."
        )
        other_text = "\n".join(self._pick_line(r, cfg) for r in others) or "None."
        brief_text = f"{overview['summary']}\n\nToday's best trade: {best_text}"
        if others:
            brief_text += "\n\nAlso on the radar: " + "; ".join(
                f"{r.name} ({r.strategy}, {r.conviction_score:.0f})" for r in others)

        from ai_analyst_service import ai_analyst_service
        outlook = ai_analyst_service.outlook_payload(ai_analyst_service.latest_outlook(db))
        if outlook:
            sectors = "; ".join(f"{s['sector']} {s['impact']:+d}" for s in outlook["sector_impacts"]) or "none flagged"
            brief_text += (f"\n\nAI next-session outlook ({outlook['session_date']}): {outlook['bias'].upper()}, "
                           f"{outlook['probability_up']:.0f}% chance NIFTY closes higher. {outlook['summary'] or ''} "
                           f"Sectors: {sectors}.")
        context = {
            "engine_version": ENGINE_VERSION,
            "outlook": outlook,
            "regime": overview["regime"],
            "best_symbol": best.symbol if best else None,
            "best_plan": ranking_service.plan_payload(best, cfg, best_rec) if best else None,
            "watchlist_top": [{"symbol": r.symbol, "name": r.name, "action": r.action,
                               "strategy": r.strategy, "conviction": r.conviction_score} for r in ranked[:5]],
            "headlines": [h["title"] for h in news[:5]],
            "ai_brief": None,
        }

        brief = existing or MorningBrief(brief_date=today_ist())
        brief.recommendation_id = best_rec.id if best_rec else None
        brief.market_summary = overview["summary"]
        brief.brief_text = brief_text
        brief.context_json = json.dumps(context, default=str)
        brief.timestamp = datetime.utcnow()
        if existing is None:
            db.add(brief)
        db.commit()
        db.refresh(brief)
        logger.info("Morning brief for %s generated: best=%s", brief.brief_date, context["best_symbol"])

        self._enqueue_ai_brief(brief.id, {
            "date": brief.brief_date.isoformat(),
            "market_summary": overview["summary"],
            "headlines": "\n".join(f"- {h}" for h in context["headlines"]) or "none",
            "best_trade": best_text,
            "other_picks": other_text,
        })
        return brief

    @staticmethod
    def _pick_line(item, cfg) -> str:
        plan = item.plan
        sizing = ranking_service.position_size(plan, cfg)
        return (f"{item.name} ({item.symbol}) — {plan.action} {plan.instrument}, {plan.strategy}. "
                f"Entry ₹{plan.entry_low:,.2f}–{plan.entry_high:,.2f}, target ₹{plan.target:,.2f}, "
                f"stop ₹{plan.stop_loss:,.2f} (1:{plan.risk_reward:.1f}), {plan.holding_period}, "
                f"{plan.risk_level} risk, conviction {item.conviction_score:.0f}/100, "
                f"qty {sizing['quantity']} at {sizing['risk_per_trade_pct']:g}% risk.")

    @staticmethod
    def _enqueue_ai_brief(brief_id: int, data: dict) -> None:
        async def job() -> None:
            from database import db_session
            text = await llm_service.generate_morning_brief(data, fallback="")
            if not text:
                return
            with db_session() as db:
                brief = db.get(MorningBrief, brief_id)
                if brief is not None:
                    context = json.loads(brief.context_json or "{}")
                    context["ai_brief"] = text
                    brief.context_json = json.dumps(context, default=str)

        llm_service.enqueue(job, label="morning-brief")

    @staticmethod
    def to_payload(brief: MorningBrief) -> dict:
        context = json.loads(brief.context_json or "{}")
        return {
            "brief_date": brief.brief_date.isoformat(),
            "generated_at": brief.timestamp.isoformat(),
            "market_summary": brief.market_summary,
            "brief_text": brief.brief_text,
            "ai_brief": context.get("ai_brief"),
            "regime": context.get("regime"),
            "best_symbol": context.get("best_symbol"),
            "best_plan": context.get("best_plan"),
            "watchlist_top": context.get("watchlist_top", []),
            "headlines": context.get("headlines", []),
        }


brief_service = BriefService()
