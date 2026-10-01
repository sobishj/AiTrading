"""
Backup & restore of everything the app knows (Settings -> Backup & restore).

All learning lives in the database — graded forecasts, shared lessons, pattern statistics, model track
records, adapted ranking weights, your trades, holdings and trader profile, the market pool, NSE history,
data sources and settings — so a backup restored on another PC gives it the same capabilities.

Not included, on purpose:
- API keys: they are kept in Windows Credential Manager, never in the database or a backup file, so a
  lost backup can't leak your accounts. Only references are kept; on another PC each cloud model shows
  "key needed" until its key is entered.
- The local model itself (e.g. Qwen in Bionic) and the .env file; install/copy those separately.

Format: a .zip with manifest.json (app, format version, row counts, columns) and one JSON-lines file per
table. Restore replaces all data (a merge would create conflicting records and double-counted outcomes,
distorting what was learned), inside one transaction, after an automatic safety backup of the current
data; it is verified against the manifest's row counts and rolled back if they don't match.
"""
import io
import json
import re
import uuid
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import Date, DateTime, Numeric, select, text

from database import Base, engine
from utils.logger import get_logger

logger = get_logger(__name__)

APP = "AiTrading"
FORMAT_VERSION = 1
BACKUP_DIR = Path(__file__).resolve().parent / "backups"
UPLOAD_DIR = BACKUP_DIR / "uploads"
CHUNK = 1000
# Housekeeping, so backups don't quietly fill the disk:
KEEP_SAFETY_BACKUPS = 3          # newest automatic "before restore" backups kept; older ones are removed
UPLOAD_MAX_AGE_SECONDS = 3600    # an uploaded file waiting for "Replace…" is dropped after an hour
MAX_LINE = 20_000_000
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.zip$")


class BackupError(Exception):
    pass


def _tables():
    import models  # noqa: F401  (registers every table on Base.metadata)
    return list(Base.metadata.sorted_tables)


def _to_json(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "tolist"):          # pgvector / numpy arrays
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_to_json(v) for v in value]
    return str(value)


def _from_json(column, value):
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, Numeric):
        return Decimal(str(value))
    return value


def _safe_api_key(value: Optional[str]) -> Optional[str]:
    """Keep only references (keyring:/env:/placeholders) — never a key itself."""
    from credential_store import is_reference
    return value if value and is_reference(value) else None


def export_to(fileobj) -> dict:
    """Write a backup zip to `fileobj`; returns the manifest."""
    tables = _tables()
    manifest = {"app": APP, "format_version": FORMAT_VERSION, "exported_at": datetime.utcnow().isoformat() + "Z",
                "tables": {}, "columns": {},
                "excluded": ["API keys (kept in Windows Credential Manager — re-enter them after restoring "
                             "on another PC)", "the local model files (install them on the new PC)",
                             "the .env file"]}
    # One REPEATABLE READ transaction: every table is read from the same moment, even while the app keeps
    # working, so a backup never pairs (say) forecasts with lessons from a different point in time.
    with zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED) as zf,             engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn, conn.begin():
        for table in tables:
            cols = [c.name for c in table.columns]
            count = 0
            with zf.open(f"tables/{table.name}.jsonl", "w") as out:
                for row in conn.execute(select(table)).mappings():
                    record = {k: _to_json(row[k]) for k in cols}
                    if table.name == "llm_profiles":
                        record["api_key"] = _safe_api_key(record.get("api_key"))
                    out.write((json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8"))
                    count += 1
            manifest["tables"][table.name] = count
            manifest["columns"][table.name] = cols
        zf.writestr("manifest.json", json.dumps(manifest, indent=1))
    return manifest


def create_backup(kind: str = "manual") -> dict:
    """Save a backup in the backups folder; returns its listing entry."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"aitrading-{kind}-{datetime.now():%Y%m%d-%H%M%S}.zip"
    path = BACKUP_DIR / name
    with open(path, "wb") as f:
        manifest = export_to(f)
    logger.info("Backup written: %s (%d rows)", name, sum(manifest["tables"].values()))
    return _entry(path, manifest)


def _entry(path: Path, manifest: Optional[dict] = None) -> dict:
    manifest = manifest or read_manifest(path)
    return {"name": path.name, "size_bytes": path.stat().st_size, "created_at": manifest.get("exported_at"),
            "rows": sum(manifest.get("tables", {}).values()), "kind": "auto" if "-auto-" in path.name else "manual"}


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    out = []
    for path in sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(_entry(path))
        except BackupError:
            continue
    return out


def backup_path(name: str) -> Path:
    if not _NAME_RE.match(name or ""):
        raise BackupError("invalid backup name")
    for folder in (BACKUP_DIR, UPLOAD_DIR):
        path = folder / name
        if path.is_file():
            return path
    raise BackupError("backup not found")


def delete_backup(name: str) -> None:
    """Delete one backup listed under "Backups on this PC" (only files in the backups folder)."""
    if not _NAME_RE.match(name or ""):
        raise BackupError("invalid backup name")
    path = BACKUP_DIR / name
    if not path.is_file():
        raise BackupError("backup not found")
    path.unlink()
    logger.info("Backup deleted: %s", name)


def _prune_uploads(keep: Optional[Path] = None) -> None:
    """Uploaded files are only a staging copy for inspect -> restore; drop stale ones."""
    if not UPLOAD_DIR.exists():
        return
    now = datetime.now().timestamp()
    for path in UPLOAD_DIR.glob("*.zip"):
        if path != keep and now - path.stat().st_mtime > UPLOAD_MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)


def _prune_safety_backups() -> None:
    if not BACKUP_DIR.exists():
        return
    safety = sorted(BACKUP_DIR.glob("aitrading-auto-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in safety[KEEP_SAFETY_BACKUPS:]:
        path.unlink(missing_ok=True)
        logger.info("Removed old safety backup %s (keeping the newest %d)", path.name, KEEP_SAFETY_BACKUPS)


def save_upload(data: bytes) -> Path:
    _prune_uploads()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"upload-{uuid.uuid4().hex[:12]}.zip"
    path.write_bytes(data)
    return path


def read_manifest(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, OSError):
        raise BackupError("This isn't an AiTrading backup file (no readable manifest).")
    if manifest.get("app") != APP:
        raise BackupError("This backup wasn't made by AiTrading.")
    return manifest


def inspect(path: Path) -> dict:
    """What a backup contains and whether it can be restored here — nothing is changed."""
    manifest = read_manifest(path)
    version = int(manifest.get("format_version") or 0)
    current = {t.name: {c.name for c in t.columns} for t in _tables()}
    tables = manifest.get("tables", {})
    unknown = sorted(set(tables) - set(current))
    missing = sorted(set(current) - set(tables))
    problems = []
    if version > FORMAT_VERSION:
        problems.append("This backup was made by a newer version of AiTrading — update this app first.")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    absent = [t for t in tables if t in current and f"tables/{t}.jsonl" not in names]
    if absent:
        problems.append(f"The file is incomplete (missing {', '.join(absent[:5])}).")
    key = {"stocks": "shares", "ai_predictions": "AI forecasts", "ai_lessons": "lesson versions",
           "pattern_stats": "pattern statistics", "trade_history": "trades", "positions": "holdings",
           "llm_profiles": "AI models", "user_chats": "chat messages", "nse_daily": "NSE daily records"}
    return {"ok": not problems, "problems": problems, "exported_at": manifest.get("exported_at"),
            "format_version": version, "rows": sum(tables.values()),
            "highlights": {label: tables.get(t, 0) for t, label in key.items()},
            "ignored_tables": unknown, "tables_not_in_backup": missing, "excluded": manifest.get("excluded", [])}


def restore(path: Path) -> dict:
    """Replace all data with the backup's (safety backup first; one transaction; verified)."""
    report = inspect(path)
    if not report["ok"]:
        raise BackupError(" ".join(report["problems"]))
    manifest = read_manifest(path)
    safety = create_backup(kind="auto-before-restore")
    _prune_safety_backups()
    tables = _tables()
    names = ", ".join(f'"{t.name}"' for t in tables)
    loaded: dict[str, int] = {}
    try:
        with engine.begin() as conn, zipfile.ZipFile(path) as zf:
            # Don't wait forever behind a long-running background job holding a table.
            conn.execute(text("SET LOCAL lock_timeout = '30s'"))
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
            for table in tables:
                entry = f"tables/{table.name}.jsonl"
                if entry not in zf.namelist():
                    continue
                columns = {c.name: c for c in table.columns}
                batch, count = [], 0
                with zf.open(entry) as f:
                    for raw in io.TextIOWrapper(f, encoding="utf-8"):
                        if len(raw) > MAX_LINE:
                            raise BackupError(f"A record in {table.name} is too large.")
                        record = json.loads(raw)
                        batch.append({k: _from_json(columns[k], v) for k, v in record.items() if k in columns})
                        if len(batch) >= CHUNK:
                            conn.execute(table.insert(), batch)
                            count += len(batch)
                            batch = []
                if batch:
                    conn.execute(table.insert(), batch)
                    count += len(batch)
                loaded[table.name] = count
                expected = manifest["tables"].get(table.name, 0)
                if count != expected:
                    raise BackupError(f"{table.name}: read {count} rows, the backup says {expected} — not restored.")
            for table in tables:
                if "id" in table.columns and table.columns["id"].primary_key:
                    conn.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM \"{table.name}\"), 0) + 1, false)"))
    except BackupError:
        raise
    except Exception as exc:  # noqa: BLE001
        if "lock" in str(exc).lower():
            raise BackupError("The app is busy with a background job (e.g. today's pick). Try again in a few "
                              "minutes — nothing was changed.")
        raise BackupError(f"Restore failed and was rolled back — nothing was changed ({exc.__class__.__name__}).")
    _after_restore()
    if path.parent == UPLOAD_DIR:
        path.unlink(missing_ok=True)   # restored; the staging copy has done its job
    needs_keys = _profiles_needing_keys()
    logger.info("Restored %d rows from %s (safety backup %s)", sum(loaded.values()), path.name, safety["name"])
    return {"restored_rows": sum(loaded.values()), "safety_backup": safety["name"], "models_needing_keys": needs_keys}


def _after_restore() -> None:
    """Make every in-memory cache follow the restored data."""
    from database import db_session
    from discovery_service import discovery_service
    from llm_service import llm_service
    from nse_service import nse_service
    from ranking_service import ranking_service
    from sources_service import sources_service

    with db_session() as db:
        sources_service.seed(db)                 # built-ins come back if the backup predates them
        nse_service.load_delivery(db)
    sources_service.invalidate()
    llm_service.reload_profiles()
    ranking_service._ranking_cache_at = None
    discovery_service._sectors_loaded = False


def _profiles_needing_keys() -> list[str]:
    from credential_store import read_secret
    from database import db_session
    from models import LLMProfile

    with db_session() as db:
        return [p.name for p in db.query(LLMProfile).all()
                if not p.is_local and p.kind != "anthropic" and not read_secret(p.api_key)
                or (p.kind == "anthropic" and p.api_key and not read_secret(p.api_key))]
