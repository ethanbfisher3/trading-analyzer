"""
launcher.pyw
------------
Double-click this (or the desktop shortcut) to start Trade Journal.
The .pyw extension tells Windows to run Python without a console window.

What happens:
  1. Checks if the app is already running on port 8501.
  2. If not, launches Streamlit in a hidden background process.
  3. Opens http://localhost:8501 in your default browser.
  4. Puts a small icon in the system tray.
     - Double-click or "Open" → brings the browser tab to the front.
     - "Quit" → stops the server and exits cleanly.
"""

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

APP_DIR = Path(__file__).parent
URL = "http://localhost:8501"
PORT = 8501

# ── Helpers ──────────────────────────────────────────────────────────────────

def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("localhost", port)) == 0


def _wait_for_server(timeout: int = 30) -> bool:
    for _ in range(timeout * 4):
        if _port_in_use(PORT):
            return True
        time.sleep(0.25)
    return False


def _make_tray_icon() -> Image.Image:
    """Draw a simple chart icon for the system tray (64×64 px)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Background circle
    d.ellipse([1, 1, size - 2, size - 2], fill="#1A1B2E")

    # Upward-trending line in green
    pts = [(10, 50), (22, 38), (34, 43), (46, 24), (56, 14)]
    d.line(pts, fill="#00C853", width=4)

    # Dots at each point
    r = 3
    for x, y in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill="#00C853")

    return img


# ── Launch ────────────────────────────────────────────────────────────────────

proc = None

if _port_in_use(PORT):
    # App already running — just open the browser
    webbrowser.open(URL)
else:
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.headless=true",
         "--browser.gatherUsageStats=false"],
        cwd=str(APP_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    def _open_when_ready():
        if _wait_for_server():
            webbrowser.open(URL)

    threading.Thread(target=_open_when_ready, daemon=True).start()


# ── System tray ───────────────────────────────────────────────────────────────

def on_open(icon, item):
    webbrowser.open(URL)


def on_quit(icon, item):
    if proc is not None:
        proc.terminate()
    icon.stop()


tray = pystray.Icon(
    name="trade_journal",
    icon=_make_tray_icon(),
    title="Trade Journal",
    menu=pystray.Menu(
        pystray.MenuItem("Open in Browser", on_open, default=True),
        pystray.MenuItem("Quit", on_quit),
    ),
)

tray.run()
