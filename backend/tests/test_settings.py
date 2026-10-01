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
