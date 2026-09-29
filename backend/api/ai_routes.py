"""
Multi-model AI analysis API: run an analysis across the selected models, read the
evidence-based consensus, per-model performance, measured factor statistics, usage
and cost, analysis mode, model presets, and the training-data export.
"""
import json
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ai_orchestrator import ai_orchestrator
from api.routes import _get_ranked_stock, _get_stock_or_404
from database import get_db
from knowledge_service import knowledge_service
from llm_service import llm_service
from models import AIConsensus, AppSettings, LLMProfile, ModelPreset
from ranking_service import ranking_service

router = APIRouter()


class AnalyzeRequest(BaseModel):
    profile_ids: Optional[list[int]] = Field(default=None, description="Models to use; default follows the mode")
    use_all: bool = Field(default=False, description="Use every enabled model")
    force: bool = Field(default=False, description="Ignore the 30-minute cache for unchanged data")


class ModeRequest(BaseModel):
    mode: Literal["single", "multi"]


class PresetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


_running: set[str] = set()


@router.post("/ai/analyze/{symbol}")
async def analyze(symbol: str, payload: AnalyzeRequest, db: Session = Depends(get_db)):
    """Every selected model analyses the same research package; claims are verified; evidence decides."""
    stock = _get_stock_or_404(db, symbol)
    if stock.symbol in _running:
        raise HTTPException(status_code=409, detail="An analysis of this stock is already running")
    _running.add(stock.symbol)
    try:
        item = await _get_ranked_stock(db, stock)
        if not item.technical.has_data:
            raise HTTPException(status_code=422, detail="No price data for this stock — nothing to analyse")
        llm_service.last_interactive_at = time.monotonic()   # background practice yields
        result = await ai_orchestrator.analyze(db, item, trigger="on_demand", profile_ids=payload.profile_ids,
                                               use_all=payload.use_all, force=payload.force)
    finally:
        _running.discard(stock.symbol)
    if result is None:
        raise HTTPException(status_code=400, detail="No AI model is selected — enable one in the model settings")
    return result


@router.get("/ai/consensus/{symbol}")
async def latest_consensus(symbol: str, db: Session = Depends(get_db)):
    stock = _get_stock_or_404(db, symbol)
    c = ai_orchestrator.latest(db, stock.id)
    return ai_orchestrator.payload(db, c) if c else {"available": False, "symbol": stock.symbol}


@router.get("/ai/consensus/{symbol}/history")
async def consensus_history(symbol: str, limit: int = 20, db: Session = Depends(get_db)):
    stock = _get_stock_or_404(db, symbol)
    rows = (db.query(AIConsensus).filter(AIConsensus.stock_id == stock.id)
            .order_by(AIConsensus.id.desc()).limit(min(limit, 100)).all())
    return [{"id": c.id, "created_at": c.created_at.isoformat() + "Z", "trigger": c.trigger, "signal": c.signal,
             "probability_up": float(c.probability_up), "evidence_confidence": float(c.confidence),
             "vote_text": json.loads(c.scores_json).get("vote_text"), "models_used": c.models_used} for c in rows]


@router.get("/ai/models/performance")
async def model_performance(db: Session = Depends(get_db)):
    """Graded record per model (and the consensus): overall, by regime/setup/sector, when dissenting, calibration."""
    return knowledge_service.model_performance(db)


@router.get("/ai/patterns")
async def patterns(source: Optional[Literal["history", "live"]] = None, min_n: int = 30,
                   db: Session = Depends(get_db)):
    """Measured factor statistics (real outcomes only)."""
    return knowledge_service.pattern_table(db, source=source, min_n=max(1, min_n))


@router.post("/ai/patterns/study")
async def run_pattern_study(db: Session = Depends(get_db)):
    return await knowledge_service.history_study(db, force=True)


@router.get("/ai/usage")
async def usage(days: int = 1, db: Session = Depends(get_db)):
    return ai_orchestrator.usage(db, days=max(1, min(days, 31)))


@router.get("/ai/mode")
async def get_mode(db: Session = Depends(get_db)):
    enabled = db.query(LLMProfile).filter(LLMProfile.enabled.is_(True)).count()
    return {"mode": ai_orchestrator.mode(db), "enabled_models": enabled}


@router.put("/ai/mode")
async def set_mode(payload: ModeRequest, db: Session = Depends(get_db)):
    cfg: AppSettings = ranking_service.get_or_create_settings(db)
    cfg.analysis_mode = payload.mode
    db.commit()
    return await get_mode(db)


# ----------------------------------------------------------------------
# Presets: built-in recipes (computed from your saved models) + your own saved configurations
# ----------------------------------------------------------------------
BUILTIN_PRESETS = [
    {"key": "local_only", "name": "Local only", "description": "Only models running on this PC. Free; nothing leaves the machine."},
    {"key": "cloud_local", "name": "Cloud + Local", "description": "Local models plus your highest-priority cloud model."},
    {"key": "max_accuracy", "name": "All models", "description": "Every saved model analyses independently (highest cost). "
                                                                 "Whether this is more accurate is measured, not assumed — see model performance."},
    {"key": "low_cost", "name": "Low cost", "description": "Local models plus the cheapest cloud model with a price entered."},
]


def _current_config(db: Session) -> dict:
    cfg = ranking_service.get_or_create_settings(db)
    return {"mode": cfg.analysis_mode, "chat_profile_id": cfg.chat_profile_id,
            "background_profile_id": cfg.background_profile_id,
            "enabled": [p.id for p in db.query(LLMProfile).filter(LLMProfile.enabled.is_(True))]}


def _builtin_enabled(db: Session, key: str) -> list[int]:
    profiles = db.query(LLMProfile).order_by(LLMProfile.priority, LLMProfile.id).all()
    local = [p.id for p in profiles if p.is_local]
    cloud = [p for p in profiles if not p.is_local]
    if key == "local_only":
        return local
    if key == "cloud_local":
        return local + [p.id for p in cloud[:1]]
    if key == "max_accuracy":
        return [p.id for p in profiles]
    priced = sorted((p for p in cloud if p.input_price is not None),
                    key=lambda p: float(p.input_price) + float(p.output_price or 0))
    return local + [p.id for p in priced[:1]]


@router.get("/ai/presets")
async def list_presets(db: Session = Depends(get_db)):
    saved = [{"id": p.id, "name": p.name, "config": json.loads(p.config_json), "created_at": p.created_at.isoformat()}
             for p in db.query(ModelPreset).order_by(ModelPreset.name)]
    names = {p.id: p.name for p in db.query(LLMProfile)}
    builtin = [{**b, "enabled": [names[i] for i in _builtin_enabled(db, b["key"]) if i in names]} for b in BUILTIN_PRESETS]
    return {"builtin": builtin, "saved": saved, "current": _current_config(db)}


def _apply(db: Session, config: dict) -> None:
    ids = set(config.get("enabled") or [])
    if not ids:
        raise HTTPException(status_code=422, detail="This preset has no available models — add a model first")
    for p in db.query(LLMProfile):
        p.enabled = p.id in ids
    cfg = ranking_service.get_or_create_settings(db)
    cfg.analysis_mode = config.get("mode") or ("multi" if len(ids) > 1 else "single")
    for field in ("chat_profile_id", "background_profile_id"):
        pid = config.get(field)
        if pid and db.get(LLMProfile, pid) is not None:
            setattr(cfg, field, pid)
    db.commit()
    llm_service.reload_profiles()


@router.post("/ai/presets/builtin/{key}/apply")
async def apply_builtin(key: str, db: Session = Depends(get_db)):
    if key not in {b["key"] for b in BUILTIN_PRESETS}:
        raise HTTPException(status_code=404, detail="Unknown preset")
    ids = _builtin_enabled(db, key)
    config = {"enabled": ids, "mode": "multi" if len(ids) > 1 else "single"}
    if key == "local_only":
        # Keep chat/background on a local model so nothing is sent to the cloud.
        cfg = ranking_service.get_or_create_settings(db)
        if ids:
            for field in ("chat_profile_id", "background_profile_id"):
                if getattr(cfg, field) not in ids:
                    config[field] = ids[0]
    _apply(db, config)
    return _current_config(db)


@router.post("/ai/presets", status_code=201)
async def save_preset(payload: PresetRequest, db: Session = Depends(get_db)):
    existing = db.query(ModelPreset).filter(ModelPreset.name == payload.name).first()
    preset = existing or ModelPreset(name=payload.name)
    preset.config_json = json.dumps(_current_config(db))
    db.add(preset)
    db.commit()
    return {"id": preset.id, "name": preset.name}


@router.post("/ai/presets/{preset_id}/apply")
async def apply_preset(preset_id: int, db: Session = Depends(get_db)):
    preset = db.get(ModelPreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    _apply(db, json.loads(preset.config_json))
    return _current_config(db)


@router.delete("/ai/presets/{preset_id}", status_code=204)
async def delete_preset(preset_id: int, db: Session = Depends(get_db)):
    preset = db.get(ModelPreset, preset_id)
    if preset is not None:
        db.delete(preset)
        db.commit()


@router.get("/ai/training-data/multi")
async def export_multi_training(graded_only: bool = True, db: Session = Depends(get_db)):
    """JSONL: every model analysis with its exact data package, answer, evidence checks, consensus and outcome."""
    def lines():
        for record in knowledge_service.training_records(db, graded_only=graded_only):
            yield json.dumps(record, default=str) + "\n"
    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Content-Disposition": "attachment; filename=aitrading-multimodel.jsonl"})
