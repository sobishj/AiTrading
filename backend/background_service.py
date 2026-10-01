"""
"Start with Windows" for background learning (Settings -> General).

Turning it on places a small hidden launcher in your Windows Startup folder that runs run-backend.ps1
at log-in (no admin rights needed, no window). Turning it off removes the launcher. The runner keeps
the backend alive and restarts it if it stops; stop-aitrading.bat stops it until the next start.
"""
import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "run-backend.ps1"
LAUNCHER_NAME = "AiTrading (background learning).vbs"


def startup_dir() -> Optional[Path]:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" if appdata else None


def launcher_path() -> Optional[Path]:
    folder = startup_dir()
    return folder / LAUNCHER_NAME if folder else None


def autostart_enabled() -> bool:
    path = launcher_path()
    return bool(path and path.exists())


def launcher_script() -> str:
    # WScript's Run with window style 0 starts PowerShell with no window at all.
    command = (f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
               f'-File ""{RUNNER}""')
    return ("' Starts AiTrading's background learning at log-in (created by AiTrading Settings -> General).\r\n"
            f'CreateObject("WScript.Shell").Run "{command}", 0, False\r\n')


def set_autostart(enabled: bool) -> bool:
    path = launcher_path()
    if path is None:
        raise RuntimeError("Windows Startup folder not found (APPDATA is not set).")
    if enabled:
        if not RUNNER.exists():
            raise RuntimeError(f"Background runner missing: {RUNNER}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(launcher_script(), encoding="utf-8", newline="")
    else:
        path.unlink(missing_ok=True)
    return autostart_enabled()


def under_runner() -> bool:
    """True when this backend was started by run-backend.ps1 (so it will be restarted if it stops)."""
    return os.environ.get("AITRADING_RUNNER") == "1"
