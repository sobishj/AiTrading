"""
Desktop notifications for holding alerts, so they reach you even when the
browser is closed. Uses the Windows toast API through PowerShell (no extra
packages); on other systems it's a no-op.
"""
import asyncio
import base64
import sys
from xml.sax.saxutils import escape

from utils.logger import get_logger

logger = get_logger(__name__)

# PowerShell's registered AppUserModelID: toasts show under "Windows PowerShell".
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
<toast scenario="{scenario}">
  <visual><binding template="ToastGeneric"><text>{title}</text><text>{body}</text></binding></visual>
  <audio src="ms-winsoundevent:Notification.{sound}"/>
</toast>
'@)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


async def notify_desktop(title: str, body: str, urgent: bool = False) -> bool:
    """Show a Windows toast. Returns False (and logs) if it couldn't be shown."""
    if sys.platform != "win32":
        return False
    script = _SCRIPT.format(
        scenario="reminder" if urgent else "default",
        sound="Looping.Alarm" if urgent else "Default",
        title=escape(title[:120]),
        body=escape(body[:300]),
        app_id=_APP_ID,
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        process = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        if process.returncode != 0:
            logger.warning("Desktop notification failed: %s", (stderr or b"").decode(errors="replace")[:300])
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Desktop notification failed: %s", exc)
        return False
