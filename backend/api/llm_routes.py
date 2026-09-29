"""
AI model settings API: saved model profiles (local or cloud), which one serves
chat vs. background work, connection tests and live model lists.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from credential_store import delete_secret, store_secret
from database import get_db
from llm_providers import PRESETS, ProviderConfig, ProviderError, mask_key, resolve_key
from llm_service import llm_service
from models import AppSettings, LLMProfile
from ranking_service import ranking_service

router = APIRouter()


class ProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kind: Literal["openai_compatible", "anthropic"]
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_key: Optional[str] = Field(default=None, max_length=500,
                                   description="Leave empty on update to keep the stored key; 'env:NAME' reads an env var")
    model: str = Field(..., min_length=1, max_length=200)
    daily_limit: int = Field(default=0, ge=0, le=100000)
    allow_practice: bool = False
    # Multi-model settings (optional; omitted fields keep their stored values on update)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=64, le=64000)
    timeout_s: Optional[int] = Field(default=None, ge=5, le=600)
    hourly_limit: int = Field(default=0, ge=0, le=100000)
    input_price: Optional[float] = Field(default=None, ge=0, le=1000)
    output_price: Optional[float] = Field(default=None, ge=0, le=1000)


class ProbeRequest(BaseModel):
    kind: Literal["openai_compatible", "anthropic"]
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    profile_id: Optional[int] = Field(default=None, description="Use this saved profile's key when api_key is empty")


class ActiveRequest(BaseModel):
    chat_profile_id: int
    background_profile_id: int


def _payload(p: LLMProfile) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "base_url": p.base_url, "api_key": mask_key(p.api_key),
            "has_key": bool(p.api_key), "model": p.model, "daily_limit": p.daily_limit,
            "allow_practice": p.allow_practice, "usage_date": p.usage_date.isoformat() if p.usage_date else None,
            "usage_count": p.usage_count, "enabled": p.enabled, "priority": p.priority,
            "temperature": float(p.temperature) if p.temperature is not None else None,
            "max_tokens": p.max_tokens, "timeout_s": p.timeout_s, "hourly_limit": p.hourly_limit or 0,
            "input_price": float(p.input_price) if p.input_price is not None else None,
            "output_price": float(p.output_price) if p.output_price is not None else None,
            "is_local": p.is_local, "key_storage": _key_storage(p.api_key)}


def _key_storage(ref: Optional[str]) -> str:
    if not ref or ref in ("not-needed", "ollama", "lm-studio"):
        return "none"
    if ref.startswith("keyring:"):
        return "credential_manager"
    if ref.startswith("env:"):
        return "environment"
    return "database"


def _config(db: Session, req: ProbeRequest) -> ProviderConfig:
    key = req.api_key
    if not key and req.profile_id:
        saved = db.get(LLMProfile, req.profile_id)
        key = saved.api_key if saved else None
    return ProviderConfig(kind=req.kind, model=req.model or "", base_url=req.base_url or None,
                          api_key=resolve_key(key), timeout=60.0)


@router.get("/llm/presets")
async def presets():
    return PRESETS


@router.get("/llm/profiles")
async def list_profiles(db: Session = Depends(get_db)):
    llm_service.active("chat")   # seeds the default local profile on first use
    cfg = ranking_service.get_or_create_settings(db)
    rows = db.query(LLMProfile).order_by(LLMProfile.id).all()
    return {"profiles": [_payload(p) for p in rows],
            "chat_profile_id": cfg.chat_profile_id, "background_profile_id": cfg.background_profile_id,
            "chat_available": await llm_service.is_available("chat"),
            "background_available": await llm_service.is_available("background")}


@router.post("/llm/profiles", status_code=201)
async def create_profile(payload: ProfileRequest, db: Session = Depends(get_db)):
    data = payload.model_dump()
    secret = data.pop("api_key", None)
    profile = LLMProfile(**data)
    db.add(profile)
    db.flush()
    # The key goes to Windows Credential Manager; the database keeps only a reference.
    profile.api_key = store_secret(f"llm-profile-{profile.id}", secret) if secret else None
    db.commit()
    db.refresh(profile)
    return _payload(profile)


@router.put("/llm/profiles/{profile_id}")
async def update_profile(profile_id: int, payload: ProfileRequest, db: Session = Depends(get_db)):
    profile = db.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Model profile not found")
    data = payload.model_dump(exclude_unset=True)
    secret = data.pop("api_key", None)
    if secret:   # empty = keep the stored key
        if secret.startswith(("env:", "keyring:")) or secret in ("not-needed", "ollama", "lm-studio"):
            delete_secret(profile.api_key if profile.api_key != secret else None)
            profile.api_key = secret
        else:
            profile.api_key = store_secret(f"llm-profile-{profile.id}", secret)
    for key, value in data.items():
        setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    llm_service.reload_profiles()
    return _payload(profile)


@router.delete("/llm/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    cfg = ranking_service.get_or_create_settings(db)
    if profile_id in (cfg.chat_profile_id, cfg.background_profile_id):
        raise HTTPException(status_code=409, detail="This model is in use — switch chat/background to another first")
    profile = db.get(LLMProfile, profile_id)
    if profile is not None:
        delete_secret(profile.api_key)
        db.delete(profile)
        db.commit()
        llm_service.reload_profiles()


@router.get("/llm/status")
async def provider_status(db: Session = Depends(get_db)):
    """Connected / unavailable for every saved model (quick parallel probe; one failing provider never blocks the rest)."""
    import asyncio
    from llm_providers import build_provider

    profiles = db.query(LLMProfile).order_by(LLMProfile.priority, LLMProfile.id).all()

    async def probe(p: LLMProfile) -> dict:
        try:
            provider = build_provider(ProviderConfig(kind=p.kind, model=p.model, base_url=p.base_url or None,
                                                     api_key=resolve_key(p.api_key), timeout=8.0))
            ok = await asyncio.wait_for(provider.ping(), timeout=10)
        except Exception:  # noqa: BLE001
            ok = False
        return {"id": p.id, "connected": bool(ok)}

    return await asyncio.gather(*(probe(p) for p in profiles))


@router.put("/llm/active")
async def set_active(payload: ActiveRequest, db: Session = Depends(get_db)):
    for pid in (payload.chat_profile_id, payload.background_profile_id):
        if db.get(LLMProfile, pid) is None:
            raise HTTPException(status_code=404, detail=f"Model profile {pid} not found")
    cfg: AppSettings = ranking_service.get_or_create_settings(db)
    cfg.chat_profile_id = payload.chat_profile_id
    cfg.background_profile_id = payload.background_profile_id
    db.commit()
    llm_service.reload_profiles()
    return llm_service.describe()


@router.post("/llm/models")
async def list_models(payload: ProbeRequest, db: Session = Depends(get_db)):
    """Models the provider offers (works before saving, so you can pick one)."""
    try:
        return {"models": await llm_service.list_models_for(_config(db, payload))}
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@router.post("/llm/test")
async def test_connection(payload: ProbeRequest, db: Session = Depends(get_db)):
    """Send one tiny request with this configuration and report the reply and latency."""
    if not payload.model:
        raise HTTPException(status_code=422, detail="Choose a model first")
    return await llm_service.test_config(_config(db, payload))


@router.put("/settings/notifications")
async def set_notifications(payload: dict, db: Session = Depends(get_db)):
    """Turn Windows notifications for holding alerts on or off: {"desktop": true|false}."""
    cfg = ranking_service.get_or_create_settings(db)
    cfg.desktop_notifications = bool(payload.get("desktop", True))
    db.commit()
    return {"desktop_notifications": cfg.desktop_notifications}
