"""
Local model runtime (Bionic) follows the model settings: it is launched when a Bionic model is
selected (enabled, or chosen for chat/background/practice) and closed when none is, so a local
model the user isn't using never runs. Windows only; elsewhere this does nothing.

start-aitrading.ps1 applies the same rule at app start; this module applies it to changes made
in Settings while the app is running.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

PROCESS_NAME = "Bionic.exe"
LAUNCH_GRACE_SECONDS = 120   # "starting" until the server answers, at most this long after a launch


def _exe() -> Optional[Path]:
    configured = os.environ.get("BIONIC_EXE")
    path = Path(configured) if configured else Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Bionic" / "Bionic.exe"
    return path if path.is_file() else None


def _windows() -> bool:
    return sys.platform == "win32"


class LocalRuntime:
    def __init__(self) -> None:
        self.wanted: Optional[bool] = None   # None until the first settings load (app start: the launcher decided)
        self.launched_at: Optional[float] = None
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    def process_running(self) -> bool:
        if not _windows():
            return False
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {PROCESS_NAME}", "/NH"], capture_output=True,
                                 text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            return PROCESS_NAME.lower() in out.lower()
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def serving() -> bool:
        try:
            return httpx.get(f"{settings.LLM_BASE_URL.rstrip('/')}/models", timeout=3).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    def sync(self, wanted: bool) -> None:
        """Called on every settings reload; acts only when the selection changes."""
        previous, self.wanted = self.wanted, wanted
        if previous is None or previous == wanted or not _windows() or "PYTEST_CURRENT_TEST" in os.environ:
            return
        if wanted:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        self.last_error = None
        if self.process_running():
            logger.info("Local model selected — Bionic is already running")
            return
        exe = _exe()
        if exe is None:
            self.last_error = "Bionic isn't installed (set BIONIC_EXE in .env if it's somewhere else)"
            logger.warning("Local model selected but %s", self.last_error)
            return
        try:
            # Detached, so it keeps running if the backend restarts.
            # Bionic is an Electron app: ELECTRON_RUN_AS_NODE (set when the backend is started from VS Code)
            # would make it exit at once, so it is not passed on.
            env = {k: v for k, v in os.environ.items() if not k.startswith("ELECTRON_")}
            subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True, env=env,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            self.launched_at = time.monotonic()
            logger.info("Local model selected — launched Bionic")
        except OSError as exc:
            self.last_error = f"couldn't launch Bionic: {exc}"
            logger.warning("Local model selected but %s", self.last_error)

    def stop(self) -> None:
        self.launched_at = None
        self.last_error = None
        if not self.process_running():
            return
        try:
            subprocess.run(["taskkill", "/IM", PROCESS_NAME, "/F"], capture_output=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            logger.info("No local model selected — closed Bionic")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Couldn't close Bionic: %s", exc)

    # ------------------------------------------------------------------
    def status(self) -> dict:
        running = self.process_running()
        serving = self.serving() if running else False
        if not _windows():
            state = "unsupported"
        elif serving:
            state = "serving"
        elif self.last_error:
            state = "error"
        elif running and self.launched_at and time.monotonic() - self.launched_at < LAUNCH_GRACE_SECONDS:
            state = "starting"
        elif running:
            state = "no_server"   # open, but its server isn't on: load the model and start the server in Bionic
        else:
            state = "stopped"
        return {"selected": bool(self.wanted), "installed": _exe() is not None, "running": running,
                "serving": serving, "state": state, "error": self.last_error}


local_runtime = LocalRuntime()
