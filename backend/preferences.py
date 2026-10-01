"""
General settings (Settings -> General), stored in app_settings so they travel with a backup.
A value left unset falls back to the config/.env default, so nothing changes until you change it.
"""
import re
from typing import Optional

from sqlalchemy.orm import Session

from config import settings

LIMITS = {
    "auto_list_size": (10, 200),
    "ai_review_shortlist": (0, 50),
    "min_traded_value_cr": (0.0, 1000.0),
}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


FIELDS = ("discovery_enabled", "universe_screen_time", "auto_list_size", "ai_review_shortlist", "min_traded_value_cr")


def _defaults() -> dict:
    return {"discovery_enabled": settings.UNIVERSE_DISCOVERY_ENABLED,
            "universe_screen_time": settings.UNIVERSE_SCREEN_TIME,
            "auto_list_size": settings.AUTO_LIST_SIZE,
            "ai_review_shortlist": settings.AI_REVIEW_SHORTLIST,
            "min_traded_value_cr": settings.MIN_TRADED_VALUE_CR}


def general(db: Optional[Session] = None) -> dict:
    """Effective general settings: the saved value, else the default."""
    saved: dict = {}
    try:
        from models import AppSettings
        if db is not None:
            row = db.get(AppSettings, 1)
            saved = {k: getattr(row, k) for k in FIELDS} if row else {}
        else:
            from database import db_session
            with db_session() as session:
                row = session.get(AppSettings, 1)
                saved = {k: getattr(row, k) for k in FIELDS} if row else {}
    except Exception:  # noqa: BLE001  (DB unavailable: defaults)
        saved = {}
    defaults = _defaults()
    value = {k: defaults[k] if saved.get(k) is None else saved[k] for k in FIELDS}
    return {"discovery_enabled": bool(value["discovery_enabled"]),
            "universe_screen_time": str(value["universe_screen_time"]),
            "auto_list_size": int(value["auto_list_size"]),
            "ai_review_shortlist": int(value["ai_review_shortlist"]),
            "min_traded_value_cr": float(value["min_traded_value_cr"]),
            "defaults": defaults}


def validate(payload: dict) -> dict:
    """Clean values to store; raises ValueError with a readable message."""
    out = {}
    if "discovery_enabled" in payload and payload["discovery_enabled"] is not None:
        out["discovery_enabled"] = bool(payload["discovery_enabled"])
    if payload.get("universe_screen_time") is not None:
        value = str(payload["universe_screen_time"]).strip()
        if not _TIME_RE.match(value):
            raise ValueError("Daily pick time must be HH:MM (24-hour, IST), e.g. 08:20")
        out["universe_screen_time"] = value
    for name, (low, high) in LIMITS.items():
        if payload.get(name) is not None:
            value = float(payload[name])
            if not low <= value <= high:
                raise ValueError(f"{name.replace('_', ' ').capitalize()} must be between {low:g} and {high:g}")
            out[name] = int(value) if isinstance(low, int) else round(value, 2)
    if out.get("ai_review_shortlist", 0) > out.get("auto_list_size", 10 ** 6):
        raise ValueError("The AI review shortlist can't be larger than the Auto list")
    return out


# ---------------------------------------------------------------------------
# Background learning: pause / resume
# ---------------------------------------------------------------------------
def learning_state() -> dict:
    """{paused, resume_at, last_learning_at}; a pause whose resume time has passed ends by itself."""
    from datetime import datetime
    try:
        from database import db_session
        from models import AppSettings
        with db_session() as db:
            row = db.get(AppSettings, 1)
            if row is None:
                return {"paused": False, "resume_at": None, "last_learning_at": None}
            if row.learning_paused and row.learning_resume_at and datetime.utcnow() >= row.learning_resume_at:
                row.learning_paused, row.learning_resume_at = False, None
            return {"paused": bool(row.learning_paused), "resume_at": row.learning_resume_at,
                    "last_learning_at": row.last_learning_at}
    except Exception:  # noqa: BLE001  (DB unavailable: report running; jobs fail on their own)
        return {"paused": False, "resume_at": None, "last_learning_at": None}


def learning_active() -> bool:
    """False while you've paused learning (Settings -> General); prices and holding alerts ignore this."""
    return not learning_state()["paused"]
