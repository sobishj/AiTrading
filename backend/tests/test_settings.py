"""Settings: general-setting validation, source URL safety, backup format and key handling — no DB, no network."""
import json
import os
import sys
import zipfile
from datetime import date, datetime
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backup_service import BackupError, _from_json, _safe_api_key, _to_json, inspect  # noqa: E402
from preferences import validate  # noqa: E402
from sources_service import _feed_name, check_url  # noqa: E402


def test_general_settings_are_validated_with_readable_errors():
    assert validate({"auto_list_size": 40, "ai_review_shortlist": 15, "universe_screen_time": "08:05",
                     "min_traded_value_cr": 12.5, "discovery_enabled": False}) == {
        "auto_list_size": 40, "ai_review_shortlist": 15, "universe_screen_time": "08:05",
        "min_traded_value_cr": 12.5, "discovery_enabled": False}
    for bad, message in (({"universe_screen_time": "8:20"}, "HH:MM"), ({"auto_list_size": 5}, "between 10"),
                         ({"auto_list_size": 20, "ai_review_shortlist": 30}, "larger than the Auto list")):
        with pytest.raises(ValueError, match=message):
            validate(bad)


def test_only_public_http_feeds_are_accepted():
    assert check_url("ftp://example.com/feed") == "use a full http:// or https:// address"
    assert "local network" in check_url("http://127.0.0.1:8000/api/status")
    assert "local network" in check_url("http://192.168.1.10/rss")
    assert "user name" in check_url("https://me:secret@example.com/rss")
    assert _feed_name("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms") == \
        "Economic Times — Markets"


def test_backup_values_round_trip_and_never_carry_api_keys():
    from types import SimpleNamespace
    from sqlalchemy import Date, DateTime, Integer, Numeric

    col = lambda t: SimpleNamespace(type=t)  # noqa: E731
    stamp, day = datetime(2026, 10, 1, 8, 20, 5), date(2026, 9, 30)
    assert _from_json(col(DateTime()), _to_json(stamp)) == stamp
    assert _from_json(col(Date()), _to_json(day)) == day
    assert _from_json(col(Numeric()), _to_json(Decimal("1514.35"))) == Decimal("1514.35")   # no float rounding
    assert _from_json(col(Integer()), 7) == 7
    assert _safe_api_key("keyring:llm-profile-6") == "keyring:llm-profile-6"   # a reference, not the key
    assert _safe_api_key("env:ANTHROPIC_API_KEY") == "env:ANTHROPIC_API_KEY"
    assert _safe_api_key("sk-ant-api03-REAL-SECRET") is None                     # a raw key never leaves


def _zip(tmp_path, manifest, entries):
    path = tmp_path / "b.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name in entries:
            zf.writestr(name, "")
    return path


def test_inspect_rejects_foreign_newer_or_incomplete_files(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(BackupError, match="isn't an AiTrading backup"):
        inspect(bad)
    with pytest.raises(BackupError, match="wasn't made by AiTrading"):
        inspect(_zip(tmp_path, {"app": "Other"}, []))
    newer = inspect(_zip(tmp_path, {"app": "AiTrading", "format_version": 99, "tables": {}}, []))
    assert not newer["ok"] and "newer version" in newer["problems"][0]
    partial = inspect(_zip(tmp_path, {"app": "AiTrading", "format_version": 1, "tables": {"stocks": 3}}, []))
    assert not partial["ok"] and "incomplete" in partial["problems"][0]


def test_backup_delete_and_housekeeping(tmp_path, monkeypatch):
    import os
    import time
    import backup_service as bs

    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(bs, "UPLOAD_DIR", tmp_path / "uploads")

    # Delete: only a valid name inside the backups folder.
    (tmp_path / "aitrading-manual-1.zip").write_bytes(b"x")
    bs.delete_backup("aitrading-manual-1.zip")
    assert not (tmp_path / "aitrading-manual-1.zip").exists()
    for bad in ("../secret.zip", "missing.zip", "notazip.txt"):
        with pytest.raises(bs.BackupError):
            bs.delete_backup(bad)

    # Only the newest safety backups are kept; manual ones are never auto-removed.
    for i in range(5):
        path = tmp_path / f"aitrading-auto-before-restore-{i}.zip"
        path.write_bytes(b"x")
        os.utime(path, (1000 + i, 1000 + i))
    (tmp_path / "aitrading-manual-2.zip").write_bytes(b"x")
    bs._prune_safety_backups()
    left = sorted(p.name for p in tmp_path.glob("*.zip"))
    assert left == ["aitrading-auto-before-restore-2.zip", "aitrading-auto-before-restore-3.zip",
                    "aitrading-auto-before-restore-4.zip", "aitrading-manual-2.zip"]

    # Stale uploaded staging copies are dropped when a new one arrives; fresh ones stay.
    (tmp_path / "uploads").mkdir()
    stale = tmp_path / "uploads" / "upload-old.zip"
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 7200, time.time() - 7200))
    fresh = bs.save_upload(b"y")
    assert not stale.exists() and fresh.exists()
