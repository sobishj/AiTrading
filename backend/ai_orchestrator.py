"""
AIOrchestrator: runs one stock analysis across one or several AI providers.

    real data (ResearchPackage) -> each model analyses independently (in parallel)
    -> EvidenceEngine checks every claim -> ConsensusEngine combines by evidence
    -> stored with the exact data and prompt -> graded later from real prices
    -> knowledge_service learns pattern / model statistics

Providers are isolated: a timeout, bad key, rate limit or unparseable answer from
one model is recorded and skipped; if every selected model fails, the local
fallback model (the background profile, e.g. Qwen) is tried. Daily and hourly
caps are enforced per provider. Models never see each other's answers from the
same run.
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from consensus_engine import combine
from credential_store import read_secret
from database import db_session
from evidence_engine import (ALL_CLAIM_KEYS, check_levels, enrich_facts, parse_claims, present_factors, verify)
from knowledge_service import knowledge_service
from llm_providers import ProviderConfig, ProviderError, build_provider
from llm_service import llm_service, record_usage
from models import AIConsensus, AIPrediction, AppSettings, LLMProfile, LLMUsage, ResearchContext
from prompts.prompt_library import PromptLibrary
from research_context import build_package, render
from trader_profile_service import trader_profile_service
from utils.logger import get_logger

logger = get_logger(__name__)

HORIZON_DAYS = 5
CACHE_MINUTES = 30                 # an unchanged research package re-uses the last on-demand run
DEFAULT_TIMEOUT_LOCAL = 180
DEFAULT_TIMEOUT_CLOUD = 120
DEFAULT_MAX_TOKENS = 900
CLOUD_CONCURRENCY = 3
PROB_FLOOR, PROB_CEILING = 10.0, 90.0
DISCLAIMER = ("Probabilities from AI analyses checked against market data — not a guarantee. "
              "You decide and place every order yourself.")

_FIELDS = ("RECOMMENDATION", "DIRECTION", "CONFIDENCE", "PROBABILITY_UP", "EXPECTED_MOVE", "ENTRY", "TARGET",
           "STOP_LOSS", "TIMEFRAME", "TECHNICAL", "NEWS", "RISKS", "REASONING")
_FIELD_RE = re.compile(r"^\W*(" + "|".join(f.replace("_", "[_ ]") for f in _FIELDS) + r")[\s*_]*[:=][\s*]*(.*)$",
                       re.IGNORECASE)


def _price(text: str) -> Optional[float]:
    m = re.search(r"\d[\d,]*(\.\d+)?", text or "")
    if not m or re.search(r"\bnone\b|\bn/?a\b", text or "", re.IGNORECASE):
        return None
    return float(m.group().replace(",", ""))


def _entry(text: str) -> Optional[float]:
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")]
    if not nums or re.search(r"\bnone\b", text or "", re.IGNORECASE):
        return None
    return round(sum(nums[:2]) / len(nums[:2]), 2)


def parse_structured(text: str) -> Optional[dict]:
    """The structured line format -> dict, or None if the answer lacks a recommendation or probability."""
    fields: dict[str, str] = {}
    last: Optional[str] = None
    for line in (text or "").splitlines():
        stripped = line.strip()
        m = _FIELD_RE.match(stripped)
        if m:
            last = m.group(1).upper().replace(" ", "_")
            fields.setdefault(last, m.group(2).strip())
        elif re.match(r"^\W*CLAIM", stripped, re.IGNORECASE):
            last = None
        elif stripped and last in ("TECHNICAL", "NEWS", "RISKS", "REASONING"):
            # a text field continued (or started) on the following line(s)
            fields[last] = (fields.get(last, "") + " " + stripped).strip()
    rec_m = re.search(r"\b(BUY|HOLD|SELL|AVOID)\b", fields.get("RECOMMENDATION", ""), re.IGNORECASE)
    prob_m = re.search(r"\d+(\.\d+)?", fields.get("PROBABILITY_UP", ""))
    if not rec_m or not prob_m:
        return None
    prob = float(prob_m.group())
    if prob <= 1.0 and "." in prob_m.group():
        prob *= 100
    prob = max(PROB_FLOOR, min(PROB_CEILING, prob))
    conf_m = re.search(r"\d+(\.\d+)?", fields.get("CONFIDENCE", ""))
    conf = None
    if conf_m:
        conf = float(conf_m.group())
        conf = conf * 100 if conf <= 1 and "." in conf_m.group() else conf
        conf = max(0.0, min(100.0, conf))
    d = fields.get("DIRECTION", "").lower()
    direction = ("up" if re.search(r"up|bull|higher", d) else "down" if re.search(r"down|bear|lower", d)
                 else "flat" if re.search(r"flat|side", d) else "up" if prob > 55 else "down" if prob < 45 else "flat")
    move_m = re.search(r"[+-]?\d+(\.\d+)?", fields.get("EXPECTED_MOVE", ""))
    move = max(-30.0, min(30.0, float(move_m.group()))) if move_m else None
    return {
        "recommendation": rec_m.group(1).upper(), "direction": direction, "confidence": conf,
        "probability_up": round(prob, 1), "expected_move_pct": move,
        "entry": _entry(fields.get("ENTRY", "")), "target": _price(fields.get("TARGET", "")),
        "stop_loss": _price(fields.get("STOP_LOSS", "")), "timeframe": fields.get("TIMEFRAME", "")[:20] or None,
        "technical": fields.get("TECHNICAL", "")[:400], "news": fields.get("NEWS", "")[:400],
        "risks": [r.strip() for r in fields.get("RISKS", "").split(";") if r.strip()][:3],
        "reasoning": fields.get("REASONING", "")[:600],
        "claims": [c.__dict__ for c in parse_claims(text)],
    }


@dataclass
class ProfileSnapshot:
    id: int
    name: str
    kind: str
    model: str
    base_url: Optional[str]
    api_key_ref: Optional[str]
    is_local: bool
    temperature: Optional[float]
    max_tokens: Optional[int]
    timeout_s: Optional[int]
    daily_limit: int
    hourly_limit: int
    updated_at: datetime

    @classmethod
    def of(cls, p: LLMProfile) -> "ProfileSnapshot":
        return cls(id=p.id, name=p.name, kind=p.kind, model=p.model, base_url=p.base_url, api_key_ref=p.api_key,
                   is_local=p.is_local, temperature=float(p.temperature) if p.temperature is not None else None,
                   max_tokens=p.max_tokens, timeout_s=p.timeout_s, daily_limit=int(p.daily_limit or 0),
                   hourly_limit=int(p.hourly_limit or 0), updated_at=p.updated_at or datetime.utcnow())

    @property
    def timeout(self) -> float:
        return float(self.timeout_s or (DEFAULT_TIMEOUT_LOCAL if self.is_local else DEFAULT_TIMEOUT_CLOUD))


def _hour(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.utcnow()).replace(minute=0, second=0, microsecond=0)


class AIOrchestrator:
    def __init__(self) -> None:
        self._local_sem = asyncio.Semaphore(1)
        self._cloud_sems: dict[int, asyncio.Semaphore] = {}
        self._providers: dict[tuple, object] = {}
        self._running: set[int] = set()

    # ------------------------------------------------------------------
    # Provider selection, caps, usage
    # ------------------------------------------------------------------
    @staticmethod
    def mode(db: Session) -> str:
        cfg = db.get(AppSettings, 1)
        return (cfg.analysis_mode if cfg else None) or "single"

    def select_profiles(self, db: Session, profile_ids: Optional[list[int]] = None,
                        use_all: bool = False) -> list[ProfileSnapshot]:
        """Explicit ids > 'use all enabled' / multi mode (enabled, by priority) > single mode (background model)."""
        q = db.query(LLMProfile)
        if profile_ids:
            rows = q.filter(LLMProfile.id.in_(profile_ids)).all()
        elif use_all or self.mode(db) == "multi":
            rows = q.filter(LLMProfile.enabled.is_(True)).all()
        else:
            cfg = db.get(AppSettings, 1)
            bg = db.get(LLMProfile, cfg.background_profile_id) if cfg and cfg.background_profile_id else None
            rows = [bg] if bg else q.order_by(LLMProfile.id).limit(1).all()
        rows = sorted((r for r in rows if r is not None), key=lambda r: (r.priority or 100, r.id))
        return [ProfileSnapshot.of(r) for r in rows]

    @staticmethod
    def fallback_profile(db: Session, exclude: set[int]) -> Optional[ProfileSnapshot]:
        """The local model to fall back to (background profile if local, else any local profile)."""
        cfg = db.get(AppSettings, 1)
        candidates = []
        if cfg and cfg.background_profile_id:
            bg = db.get(LLMProfile, cfg.background_profile_id)
            if bg is not None:
                candidates.append(bg)
        candidates += db.query(LLMProfile).order_by(LLMProfile.priority, LLMProfile.id).all()
        for p in candidates:
            if p.is_local and p.id not in exclude:
                return ProfileSnapshot.of(p)
        return None

    @staticmethod
    def _reserve(profile: ProfileSnapshot) -> Optional[str]:
        """Count one request against the daily and hourly caps; returns a reason when a cap is reached."""
        from llm_service import IST
        today: date = datetime.now(IST).date()
        with db_session() as db:
            row = db.get(LLMProfile, profile.id)
            if row is None:
                return "profile deleted"
            if row.usage_date != today:
                row.usage_date, row.usage_count = today, 0
            if row.daily_limit and row.usage_count >= row.daily_limit:
                return f"daily cap of {row.daily_limit} requests reached"
            if row.hourly_limit:
                used = (db.query(LLMUsage).filter(LLMUsage.profile_id == profile.id, LLMUsage.hour == _hour()).first())
                if used and used.requests >= row.hourly_limit:
                    return f"hourly cap of {row.hourly_limit} requests reached"
            row.usage_count += 1
        return None

    @staticmethod
    def record_usage(profile_id: int, ok: bool, input_tokens: int = 0, output_tokens: int = 0) -> None:
        record_usage(profile_id, ok, input_tokens, output_tokens)

    def _provider(self, profile: ProfileSnapshot):
        key = (profile.id, profile.updated_at)
        provider = self._providers.get(key)
        if provider is None:
            provider = build_provider(ProviderConfig(kind=profile.kind, model=profile.model,
                                                     base_url=profile.base_url or None,
                                                     api_key=read_secret(profile.api_key_ref) or None,
                                                     timeout=profile.timeout))
            self._providers = {k: v for k, v in self._providers.items() if k[0] != profile.id}
            self._providers[key] = provider
        return provider

    def _semaphore(self, profile: ProfileSnapshot) -> asyncio.Semaphore:
        if profile.is_local:
            return self._local_sem   # one local inference at a time (a local runtime serves requests serially)
        return self._cloud_sems.setdefault(profile.id, asyncio.Semaphore(CLOUD_CONCURRENCY))

    async def call_model(self, profile: ProfileSnapshot, prompt: str) -> dict:
        """One isolated model call with caps, timeout and a single retry for transient errors."""
        result = {"profile_id": profile.id, "name": profile.name, "model": profile.model, "ok": False,
                  "error": None, "text": None, "latency_ms": None, "input_tokens": 0, "output_tokens": 0}
        reason = self._reserve(profile)
        if reason:
            result["error"] = reason
            return result
        messages = [{"role": "user", "content": prompt}]
        for attempt in (1, 2):
            started = time.monotonic()
            try:
                async with self._semaphore(profile):
                    reply = await asyncio.wait_for(
                        self._provider(profile).chat_ex(messages, max_tokens=profile.max_tokens or DEFAULT_MAX_TOKENS,
                                                        temperature=profile.temperature if profile.temperature is not None else 0.2,
                                                        effort="medium", timeout=profile.timeout),
                        timeout=profile.timeout + 15)
                result.update(ok=bool(reply.text), text=reply.text, input_tokens=reply.input_tokens,
                              output_tokens=reply.output_tokens, latency_ms=int((time.monotonic() - started) * 1000),
                              error=None if reply.text else "empty answer")
                self.record_usage(profile.id, bool(reply.text), reply.input_tokens, reply.output_tokens)
                llm_service.note_credit(profile.id)
                return result
            except asyncio.TimeoutError:
                result["error"] = f"timed out after {profile.timeout:.0f}s"
                retry = False
            except ProviderError as exc:
                result["error"] = exc.message
                llm_service.note_credit(profile.id, exc)
                retry = exc.retryable and "rate limit" not in exc.message.lower() and not exc.no_credit
            except Exception as exc:  # noqa: BLE001  (isolate anything unexpected; never echo request data)
                result["error"] = f"unexpected {exc.__class__.__name__}"
                retry = False
            self.record_usage(profile.id, False)
            logger.warning("AI provider %s failed (attempt %d): %s", profile.name, attempt, result["error"])
            if not retry or attempt == 2:
                break
            await asyncio.sleep(2)
        return result

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    async def analyze(self, db: Session, item, trigger: str = "on_demand", profile_ids: Optional[list[int]] = None,
                      use_all: bool = False, force: bool = False) -> Optional[dict]:
        from ai_analyst_service import ai_analyst_service, today_ist
        from market_service import market_service
        from ranking_service import ranking_service

        overview = await market_service.get_market_overview()
        package = build_package(item, overview, market_service.fetch_news(), market_service.data_source,
                                top_sectors=ranking_service.context.top_sectors)
        package.facts = enrich_facts(package.facts, package.engine)
        content_hash = package.content_hash()
        selected = self.select_profiles(db, profile_ids, use_all)
        if not selected:
            return None

        if not force:
            recent = (db.query(AIConsensus).join(ResearchContext, AIConsensus.context_id == ResearchContext.id)
                      .filter(AIConsensus.stock_id == item.stock_id, ResearchContext.content_hash == content_hash,
                              AIConsensus.created_at >= datetime.utcnow() - timedelta(minutes=CACHE_MINUTES))
                      .order_by(AIConsensus.id.desc()).first())
            if recent is not None and set(json.loads(recent.scores_json).get("profile_ids", [])) == {p.id for p in selected}:
                return self.payload(db, recent)

        regimes = package.facts.get("regimes") or []
        regime = regimes[0] if regimes else None
        factors = present_factors(package.facts)
        knowledge = knowledge_service.knowledge_block(db, item.stock_id, factors, regime)
        _, lessons = ai_analyst_service.current_lessons(db)
        prompt = PromptLibrary.structured_analysis(
            name=item.name, symbol=item.symbol, horizon=HORIZON_DAYS, collected_at=package.collected_at,
            data_source=package.data_source, context=render(package), knowledge=knowledge,
            lessons=ai_analyst_service.lessons_block(lessons),
            trader=trader_profile_service.block(db, item.symbol), claim_keys=", ".join(ALL_CLAIM_KEYS))
        ctx = ResearchContext(stock_id=item.stock_id, content_hash=content_hash,
                              context_json=json.dumps(package.to_json(), default=str), prompt_text=prompt,
                              data_timestamp=_parse_ts(package.data_timestamp))
        db.add(ctx)
        db.commit()

        results = list(await asyncio.gather(*(self.call_model(p, prompt) for p in selected)))
        parsed_ok = [r for r in results if r["ok"] and parse_structured(r["text"])]
        if not parsed_ok:
            fb = self.fallback_profile(db, exclude={p.id for p in selected})
            if fb is not None:
                logger.info("All selected models failed for %s — falling back to %s", item.symbol, fb.name)
                fb_result = await self.call_model(fb, prompt)
                fb_result["fallback"] = True
                results.append(fb_result)

        headlines = [h.__dict__ for h in package.headlines]
        members, failed = [], []
        for r in results:
            parsed = parse_structured(r["text"]) if r["ok"] else None
            if parsed is None:
                failed.append({"model": r["model"], "name": r["name"], "profile_id": r["profile_id"],
                               "error": r["error"] or "answer not in the required format",
                               "excerpt": (r["text"] or "")[:200] if r["ok"] else None})
                continue
            from evidence_engine import Claim
            claims = [Claim(**c) for c in parsed["claims"]]
            report = verify(claims, package.facts, headlines, parsed["recommendation"], parsed["news"],
                            predictions={"probability_up": parsed["probability_up"],
                                         "expected_move_pct": parsed["expected_move_pct"],
                                         "target": parsed["target"], "stop_loss": parsed["stop_loss"]},
                            interpretation=" ".join(x for x in (parsed["technical"], parsed["reasoning"]) if x))
            issues = check_levels(parsed["recommendation"], package.facts.get("price"), package.facts.get("atr"),
                                  parsed["entry"], parsed["target"], parsed["stop_loss"])
            members.append({**parsed, "model": r["model"], "name": r["name"], "profile_id": r["profile_id"],
                            "evidence": report.to_json(), "level_issues": issues, "latency_ms": r["latency_ms"],
                            "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
                            "fallback": r.get("fallback", False), "raw": r["text"],
                            "reliability_stats": knowledge_service.model_reliability(db, r["model"], regime,
                                                                                     item.strategy)})

        result = combine(members, knowledge_service.factor_stats_for(db, factors, regime), package.facts,
                         package.engine)
        if result is None:
            logger.warning("No usable AI analysis for %s (%d provider(s) failed)", item.symbol, len(failed))
            return {"symbol": item.symbol, "available": False, "failed": failed, "context_id": ctx.id,
                    "collected_at": package.collected_at, "disclaimer": DISCLAIMER}

        weights = {m["profile_id"]: m for m in result.members}
        consensus = AIConsensus(
            stock_id=item.stock_id, context_id=ctx.id, trigger=trigger, signal=result.signal,
            probability_up=result.probability_up, confidence=result.confidence,
            votes_json=json.dumps(result.votes),
            scores_json=json.dumps({**result.scores, "members": result.members, "vote_text": result.vote_text,
                                    "profile_ids": [p.id for p in selected]}),
            levels_json=json.dumps(result.levels),
            reasoning_json=json.dumps({"reasoning": result.reasoning, "evidence_for": result.evidence_for,
                                       "risks": result.risks, "historical": result.historical}),
            disagreement_json=json.dumps(result.disagreement), models_used=len(members),
            models_failed_json=json.dumps(failed), market_regime=",".join(regimes) or None,
        )
        db.add(consensus)
        db.flush()

        today = today_ist()
        version, _ = ai_analyst_service.current_lessons(db)
        common = dict(stock_id=item.stock_id, prediction_date=today, kind="live", horizon_days=HORIZON_DAYS,
                      price_at_prediction=package.facts.get("price"), strategy=item.strategy,
                      conviction_at_prediction=item.conviction_score, lessons_version=version,
                      consensus_id=consensus.id, context_id=ctx.id, market_regime=consensus.market_regime)
        for m in members:
            row = (db.query(AIPrediction).filter(AIPrediction.stock_id == item.stock_id, AIPrediction.kind == "live",
                                                 AIPrediction.prediction_date == today, AIPrediction.role == "member",
                                                 AIPrediction.profile_id == m["profile_id"]).first()
                   or AIPrediction(role="member", profile_id=m["profile_id"], **common))
            for k, v in common.items():
                setattr(row, k, v)
            w = weights.get(m["profile_id"], {})
            row.direction, row.probability_up = m["direction"], m["probability_up"]
            row.expected_move_pct, row.reason = m["expected_move_pct"], (m["reasoning"] or "")[:400]
            row.recommendation, row.confidence = m["recommendation"], m["confidence"]
            row.entry, row.target, row.stop_loss, row.timeframe = m["entry"], m["target"], m["stop_loss"], m["timeframe"]
            row.model, row.prompt_text, row.raw_response = m["model"], None, m["raw"]
            row.evidence_score, row.latency_ms = m["evidence"]["evidence_score"], m["latency_ms"]
            row.analysis_json = json.dumps({k: m[k] for k in (
                "name", "recommendation", "direction", "confidence", "probability_up", "expected_move_pct", "entry",
                "target", "stop_loss", "timeframe", "technical", "news", "risks", "reasoning", "claims", "evidence",
                "level_issues", "input_tokens", "output_tokens", "fallback")}
                | {"weight": w.get("weight"), "reliability_why": w.get("reliability_why"),
                   "evidence_why": w.get("evidence_why")})
            row.created_at = datetime.utcnow()
            row.graded_at = None
            db.add(row)

        role = "primary" if trigger == "daily" else "adhoc"
        prob = result.probability_up
        row = (db.query(AIPrediction).filter(AIPrediction.stock_id == item.stock_id, AIPrediction.kind == "live",
                                             AIPrediction.prediction_date == today, AIPrediction.role == role,
                                             AIPrediction.model == "consensus").first()
               or AIPrediction(role=role, **common))
        for k, v in common.items():
            setattr(row, k, v)
        row.model, row.profile_id = "consensus", None
        row.direction = "up" if prob > 55 else "down" if prob < 45 else "flat"
        row.probability_up, row.recommendation, row.confidence = prob, result.signal, result.confidence
        moves = [m["expected_move_pct"] for m in members if m["expected_move_pct"] is not None]
        row.expected_move_pct = round(sum(moves) / len(moves), 2) if moves else None
        row.entry, row.target, row.stop_loss = (result.levels.get("entry"), result.levels.get("target"),
                                                result.levels.get("stop_loss"))
        row.reason = f"Consensus of {result.vote_text}: " + (result.reasoning[-1] if result.reasoning else "")[:300]
        row.evidence_score = round(result.scores["evidence_support"] / 100, 3)
        row.prompt_text, row.raw_response, row.analysis_json = None, None, None
        row.created_at, row.graded_at = datetime.utcnow(), None
        db.add(row)
        db.commit()
        logger.info("AI analysis %s: %s (%s), %d model(s), %d failed", item.symbol, result.signal,
                    result.vote_text, len(members), len(failed))
        return self.payload(db, consensus)

    def rescore(self, db: Session, consensus_id: int) -> Optional[dict]:
        """
        Recompute a stored run's consensus from the models' saved answers and evidence checks
        (no model is called again) — used after the combination rules change.
        """
        c = db.get(AIConsensus, consensus_id)
        ctx = db.get(ResearchContext, c.context_id) if c and c.context_id else None
        rows = (db.query(AIPrediction).filter(AIPrediction.consensus_id == consensus_id,
                                              AIPrediction.role == "member").all()) if c else []
        if c is None or ctx is None or not rows:
            return None
        package = json.loads(ctx.context_json)
        facts = package.get("facts", {})
        regimes = facts.get("regimes") or []
        regime = regimes[0] if regimes else None
        strategy = rows[0].strategy
        members = []
        for p in rows:
            a = json.loads(p.analysis_json) if p.analysis_json else {}
            members.append({**a, "model": p.model, "profile_id": p.profile_id,
                            "recommendation": p.recommendation, "probability_up": float(p.probability_up),
                            "reliability_stats": knowledge_service.model_reliability(db, p.model, regime, strategy)})
        result = combine(members, knowledge_service.factor_stats_for(db, present_factors(facts), regime), facts,
                         package.get("engine"))
        weights = {m["profile_id"]: m for m in result.members}
        for p in rows:
            a = json.loads(p.analysis_json) if p.analysis_json else {}
            w = weights.get(p.profile_id, {})
            a.update(weight=w.get("weight"), reliability_why=w.get("reliability_why"), evidence_why=w.get("evidence_why"))
            p.analysis_json = json.dumps(a)
        scores = json.loads(c.scores_json)
        c.signal, c.probability_up, c.confidence = result.signal, result.probability_up, result.confidence
        c.scores_json = json.dumps({**result.scores, "members": result.members, "vote_text": result.vote_text,
                                    "profile_ids": scores.get("profile_ids", [])})
        c.levels_json = json.dumps(result.levels)
        c.reasoning_json = json.dumps({"reasoning": result.reasoning, "evidence_for": result.evidence_for,
                                       "risks": result.risks, "historical": result.historical})
        c.disagreement_json = json.dumps(result.disagreement)
        cons_row = (db.query(AIPrediction).filter(AIPrediction.consensus_id == consensus_id,
                                                  AIPrediction.model == "consensus").first())
        if cons_row is not None and cons_row.graded_at is None:
            prob = result.probability_up
            cons_row.direction = "up" if prob > 55 else "down" if prob < 45 else "flat"
            cons_row.probability_up, cons_row.recommendation, cons_row.confidence = prob, result.signal, result.confidence
            cons_row.entry, cons_row.target, cons_row.stop_loss = (result.levels.get("entry"), result.levels.get("target"),
                                                                   result.levels.get("stop_loss"))
            cons_row.evidence_score = round(result.scores["evidence_support"] / 100, 3)
        db.commit()
        return self.payload(db, c)

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------
    def latest(self, db: Session, stock_id: int, max_age_days: int = 3) -> Optional[AIConsensus]:
        return (db.query(AIConsensus).filter(AIConsensus.stock_id == stock_id,
                                             AIConsensus.created_at >= datetime.utcnow() - timedelta(days=max_age_days))
                .order_by(AIConsensus.id.desc()).first())

    @staticmethod
    def payload(db: Session, c: AIConsensus) -> dict:
        ctx = db.get(ResearchContext, c.context_id) if c.context_id else None
        context = json.loads(ctx.context_json) if ctx else {}
        scores = json.loads(c.scores_json)
        reasoning = json.loads(c.reasoning_json)
        members = []
        for p in (db.query(AIPrediction).filter(AIPrediction.consensus_id == c.id, AIPrediction.role == "member")
                  .order_by(AIPrediction.id)):
            a = json.loads(p.analysis_json) if p.analysis_json else {}
            members.append({
                "profile_id": p.profile_id, "name": a.get("name"), "model": p.model,
                "recommendation": p.recommendation, "direction": p.direction,
                "stated_confidence": float(p.confidence) if p.confidence is not None else None,
                "probability_up": float(p.probability_up),
                "expected_move_pct": float(p.expected_move_pct) if p.expected_move_pct is not None else None,
                "entry": a.get("entry"), "target": a.get("target"), "stop_loss": a.get("stop_loss"),
                "timeframe": a.get("timeframe"), "technical": a.get("technical"), "news": a.get("news"),
                "risks": a.get("risks") or [], "reasoning": a.get("reasoning"),
                "evidence": a.get("evidence"), "level_issues": a.get("level_issues") or [],
                "weight": a.get("weight"), "reliability_why": a.get("reliability_why"),
                "evidence_why": a.get("evidence_why"), "latency_ms": p.latency_ms, "fallback": a.get("fallback"),
                "tokens": {"input": a.get("input_tokens", 0), "output": a.get("output_tokens", 0)},
                "outcome": None if p.graded_at is None else {
                    "return_pct": float(p.actual_return_pct), "correct": p.correct},
            })
        return {
            "id": c.id, "available": True, "symbol": c.stock.symbol if c.stock else None,
            "created_at": c.created_at.isoformat() + "Z", "trigger": c.trigger,
            "signal": c.signal, "probability_up": float(c.probability_up), "evidence_confidence": float(c.confidence),
            "votes": json.loads(c.votes_json), "vote_text": scores.get("vote_text"),
            "scores": {k: v for k, v in scores.items() if k not in ("members", "vote_text", "profile_ids")},
            "levels": json.loads(c.levels_json) if c.levels_json else None,
            "evidence_for": reasoning.get("evidence_for", []), "risks": reasoning.get("risks", []),
            "historical": reasoning.get("historical", []), "reasoning": reasoning.get("reasoning", []),
            "disagreement": json.loads(c.disagreement_json) if c.disagreement_json else [],
            "members": members, "failed": json.loads(c.models_failed_json) if c.models_failed_json else [],
            "market_regime": c.market_regime, "context_id": c.context_id,
            "data_timestamp": context.get("data_timestamp"), "collected_at": context.get("collected_at"),
            "data_source": context.get("data_source"),
            "headlines": context.get("headlines", []),
            "disclaimer": DISCLAIMER,
        }

    def usage(self, db: Session, days: int = 1) -> list[dict]:
        since = _hour() - timedelta(days=days) + timedelta(hours=1)
        out = []
        for p in db.query(LLMProfile).order_by(LLMProfile.priority, LLMProfile.id):
            rows = db.query(LLMUsage).filter(LLMUsage.profile_id == p.id, LLMUsage.hour >= since).all()
            this_hour = next((r for r in rows if r.hour == _hour()), None)
            tin, tout = sum(r.input_tokens for r in rows), sum(r.output_tokens for r in rows)
            cost = None
            if p.input_price is not None or p.output_price is not None:
                cost = round(tin / 1e6 * float(p.input_price or 0) + tout / 1e6 * float(p.output_price or 0), 4)
            out.append({"profile_id": p.id, "name": p.name, "model": p.model, "is_local": p.is_local,
                        "requests": sum(r.requests for r in rows), "failures": sum(r.failures for r in rows),
                        "requests_this_hour": this_hour.requests if this_hour else 0,
                        "input_tokens": tin, "output_tokens": tout, "estimated_cost_usd": cost,
                        "daily_limit": p.daily_limit, "hourly_limit": p.hourly_limit or 0,
                        "used_today": p.usage_count or 0})
        return out


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return ts.replace(tzinfo=None) if ts.tzinfo is None else ts.astimezone(tz=None).replace(tzinfo=None)
    except ValueError:
        return None


ai_orchestrator = AIOrchestrator()
