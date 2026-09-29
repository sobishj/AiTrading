"""
API-key storage in the operating system's credential vault (Windows Credential
Manager via `keyring`), so keys are not kept in the database, the repo, logs
or the browser.

The llm_profiles.api_key column holds only a *reference*:
- "keyring:<name>"  -> the secret lives in the OS vault under service SERVICE
- "env:<VAR>"       -> read from an environment variable
- local placeholders ("not-needed", "ollama", "lm-studio") are not secrets and
  stay as they are.
If no OS vault is available the key stays in the database (logged as a warning).
"""
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

SERVICE = "AiTrading"
PLACEHOLDERS = {"", "not-needed", "ollama", "lm-studio"}


def _keyring():
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
        return keyring
    except Exception:  # noqa: BLE001
        return None


def is_reference(value: Optional[str]) -> bool:
    return bool(value) and (value.startswith("keyring:") or value.startswith("env:") or value in PLACEHOLDERS)


def store_secret(name: str, secret: str) -> str:
    """Save a secret; returns what to keep in the database (a reference, or the secret as a last resort)."""
    if not secret or secret in PLACEHOLDERS or secret.startswith(("env:", "keyring:")):
        return secret
    kr = _keyring()
    if kr is None:
        logger.warning("No OS credential vault available — API key for %s stays in the local database", name)
        return secret
    kr.set_password(SERVICE, name, secret)
    return f"keyring:{name}"


def read_secret(reference: Optional[str]) -> Optional[str]:
    """Resolve a stored reference to the actual secret (never logged)."""
    import os
    if not reference:
        return None
    if reference.startswith("env:"):
        return os.environ.get(reference[4:].strip()) or None
    if reference.startswith("keyring:"):
        kr = _keyring()
        return kr.get_password(SERVICE, reference[8:]) if kr else None
    return reference


def delete_secret(reference: Optional[str]) -> None:
    if reference and reference.startswith("keyring:"):
        kr = _keyring()
        if kr is not None:
            try:
                kr.delete_password(SERVICE, reference[8:])
            except Exception:  # noqa: BLE001  (already gone)
                pass


def migrate_plaintext_keys() -> int:
    """Move any API keys still stored in plain text in llm_profiles into the OS vault."""
    from database import db_session
    from models import LLMProfile

    moved = 0
    if _keyring() is None:
        return 0
    with db_session() as db:
        for profile in db.query(LLMProfile).all():
            if profile.api_key and not is_reference(profile.api_key):
                profile.api_key = store_secret(f"llm-profile-{profile.id}", profile.api_key)
                moved += 1
    if moved:
        logger.info("Moved %d API key(s) from the database into Windows Credential Manager", moved)
    return moved
