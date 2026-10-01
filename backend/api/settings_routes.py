"""
Settings window: data sources, general settings, backup & restore, and per-model cost estimates.
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import AppSettings, DataSource, LLMProfile, LLMUsage
from sources_service import check_url, sources_service, test_feed

router = APIRouter()

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
# Typical tokens per call when a model has no usage history yet (an analysis prompt is the big one).
TYPICAL_INPUT_TOKENS, TYPICAL_OUTPUT_TOKENS = 2500, 450


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
class FeedRequest(BaseModel):
    url: str
    name: Optional[str] = None


class SourceUpdate(BaseModel):
    enabled: Optional[bool] = None
    name: Optional[str] = None


def _source_payload(s: DataSource) -> dict:
    return {"id": s.id, "kind": s.kind, "name": s.name, "url": s.url, "enabled": s.enabled, "builtin": s.builtin,
            "notes": s.notes, "last_ok_at": s.last_ok_at.isoformat() + "Z" if s.last_ok_at else None,
            "last_error": s.last_error, "last_items": s.last_items}


@router.get("/sources")
async def list_sources(db: Session = Depends(get_db)):
    sources_service.seed(db)
    rows = db.query(DataSource).order_by(DataSource.kind != "rss", DataSource.builtin.desc(), DataSource.id).all()
    return [_source_payload(s) for s in rows]


@router.post("/sources/test")
async def test_source(payload: FeedRequest):
    """Fetch a feed and show what it contains — nothing is saved."""
    return await test_feed(payload.url)


@router.post("/sources", status_code=201)
async def add_source(payload: FeedRequest, db: Session = Depends(get_db)):
    url = payload.url.strip()
    if db.query(DataSource).filter(DataSource.url == url).first():
        raise HTTPException(status_code=409, detail="This feed is already in your sources.")
    result = await test_feed(url)
    if not result["ok"]:
        raise HTTPException(status_code=422, detail=result["error"])
    source = DataSource(kind="rss", name=(payload.name or result["title"]).strip()[:120], url=url, enabled=True,
                        builtin=False, last_ok_at=datetime.utcnow(), last_items=result["count"])
    db.add(source)
    db.commit()
    sources_service.invalidate()
    return _source_payload(source)


@router.patch("/sources/{source_id}")
async def update_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if payload.enabled is not None:
        source.enabled = payload.enabled
    if payload.name and payload.name.strip():
        source.name = payload.name.strip()[:120]
    db.commit()
    sources_service.invalidate()
    if source.kind.startswith("nse_"):
        from nse_service import nse_service
        if source.kind == "nse_bhavcopy":
            nse_service.load_delivery(db)
        else:
            asyncio.create_task(nse_service.refresh())
    return _source_payload(source)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, db: Session = Depends(get_db)):
    source = db.get(DataSource, source_id)
    if source is None:
        return
    if source.builtin:
        raise HTTPException(status_code=400, detail="Built-in sources can be switched off, not deleted.")
    db.delete(source)
    db.commit()
    sources_service.invalidate()


@router.post("/sources/restore-defaults")
async def restore_default_sources(db: Session = Depends(get_db)):
    """Switch every built-in source back on (feeds you added are kept as they are)."""
    for s in db.query(DataSource).filter(DataSource.builtin.is_(True)):
        s.enabled = True
    db.commit()
    sources_service.seed(db)
    sources_service.invalidate()
    return await list_sources(db)


# ---------------------------------------------------------------------------
# General settings
# ---------------------------------------------------------------------------
class GeneralRequest(BaseModel):
    discovery_enabled: Optional[bool] = None
    universe_screen_time: Optional[str] = None
    auto_list_size: Optional[int] = None
    ai_review_shortlist: Optional[int] = None
    min_traded_value_cr: Optional[float] = None


@router.get("/settings/general")
async def get_general(db: Session = Depends(get_db)):
    from preferences import general
    return general(db)


@router.put("/settings/general")
async def put_general(payload: GeneralRequest, db: Session = Depends(get_db)):
    from preferences import general, validate
    from ranking_service import ranking_service

    merged = {**{k: v for k, v in general(db).items() if k != "defaults"},
              **payload.model_dump(exclude_none=True)}
    try:
        clean = validate(merged)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    cfg = ranking_service.get_or_create_settings(db)
    for key, value in clean.items():
        if key in payload.model_dump(exclude_none=True):
            setattr(cfg, key, value)
    db.commit()
    return general(db)


# ---------------------------------------------------------------------------
# Cost estimate per model (for the label next to the daily cap)
# ---------------------------------------------------------------------------
@router.get("/llm/cost-stats")
async def cost_stats(db: Session = Depends(get_db)):
    """
    Per model: price, average tokens per successful call over the last 7 days (or typical values when it
    has no history), and the recent average spend per day — the frontend multiplies by the cap you type.
    """
    from api.llm_routes import _usd_inr
    from llm_providers import list_price

    fx = await _usd_inr()
    since = datetime.utcnow() - timedelta(days=7)
    out = []
    for p in db.query(LLMProfile).order_by(LLMProfile.id):
        rows = db.query(LLMUsage).filter(LLMUsage.profile_id == p.id, LLMUsage.hour >= since).all()
        ok_calls = sum(r.requests - r.failures for r in rows)
        tin, tout = sum(r.input_tokens for r in rows), sum(r.output_tokens for r in rows)
        if p.input_price is not None or p.output_price is not None:
            price, source = (float(p.input_price or 0), float(p.output_price or 0)), "model settings"
        else:
            price, source = list_price(p.model), "list price"
        measured = ok_calls >= 5 and tin > 0
        per_in = tin / ok_calls if measured else TYPICAL_INPUT_TOKENS
        per_out = tout / ok_calls if measured else TYPICAL_OUTPUT_TOKENS
        per_call_usd = (per_in / 1e6 * price[0] + per_out / 1e6 * price[1]) if price else None
        days = max(1, min(7, (datetime.utcnow() - min((r.hour for r in rows), default=datetime.utcnow())).days + 1))
        recent_usd = ((tin / 1e6 * price[0] + tout / 1e6 * price[1]) / days) if price and rows else None
        out.append({"profile_id": p.id, "is_local": p.is_local, "fx": fx,
                    "price": {"input": price[0], "output": price[1], "source": source} if price else None,
                    "tokens_per_call": {"input": round(per_in), "output": round(per_out),
                                        "measured_from_calls": ok_calls if measured else 0},
                    "usd_per_call": round(per_call_usd, 5) if per_call_usd is not None else None,
                    "recent_usd_per_day": round(recent_usd, 4) if recent_usd is not None else None,
                    "recent_calls_per_day": round(sum(r.requests for r in rows) / days, 1) if rows else 0})
    return out


# ---------------------------------------------------------------------------
# Backup & restore
# ---------------------------------------------------------------------------
class RestoreRequest(BaseModel):
    name: str


@router.get("/backup/list")
async def backup_list():
    from backup_service import list_backups
    return await asyncio.to_thread(list_backups)


@router.post("/backup/create")
async def backup_create():
    from backup_service import create_backup
    return await asyncio.to_thread(create_backup, "manual")


@router.get("/backup/download/{name}")
async def backup_download(name: str):
    from backup_service import BackupError, backup_path
    try:
        path = backup_path(name)
    except BackupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.delete("/backup/{name}", status_code=204)
async def backup_delete(name: str):
    from backup_service import BackupError, delete_backup
    try:
        await asyncio.to_thread(delete_backup, name)
    except BackupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/backup/inspect")
async def backup_inspect(file: UploadFile = File(...)):
    """Upload a backup and see what it contains; nothing is changed until you confirm the restore."""
    from backup_service import BackupError, inspect, save_upload
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 500 MB.")
    path = save_upload(data)
    try:
        report = await asyncio.to_thread(inspect, path)
    except BackupError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc))
    return {**report, "name": path.name}


@router.post("/backup/inspect/{name}")
async def backup_inspect_saved(name: str):
    from backup_service import BackupError, backup_path, inspect
    try:
        return {**await asyncio.to_thread(inspect, backup_path(name)), "name": name}
    except BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/backup/restore")
async def backup_restore(payload: RestoreRequest):
    from backup_service import BackupError, backup_path, restore
    from discovery_service import discovery_service
    if discovery_service.running:
        raise HTTPException(status_code=409, detail="Today's pick is running — restore when it has finished.")
    try:
        return await asyncio.to_thread(restore, backup_path(payload.name))
    except BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
