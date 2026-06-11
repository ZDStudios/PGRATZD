"""Local screen-capture agent (Python).

Run this on your own computer. It grabs your screen a few times a second,
shrinks/compresses each frame to a JPEG, and pushes it to the Render server
over a WebSocket. Whoever opens the dashboard sees your screen live.

Configure with environment variables:
  SERVER_URL    wss://your-app.onrender.com   (required)
  STREAM_TOKEN  your-secret-token              (required, must match server)
  FPS           frames per second              (default 4)
  MAX_WIDTH     downscale width in px          (default 1280)
  QUALITY       JPEG quality 1-100             (default 60)

Install dependencies first:  pip install -r requirements.txt
"""

import io
import os
import sys
import time
from urllib.parse import quote

import mss
import websocket  # from the 'websocket-client' package
from PIL import Image

SERVER_URL = os.environ.get("SERVER_URL")
TOKEN = os.environ.get("STREAM_TOKEN")
FPS = float(os.environ.get("FPS", "4"))
MAX_WIDTH = int(os.environ.get("MAX_WIDTH", "1280"))
QUALITY = int(os.environ.get("QUALITY", "60"))

if not SERVER_URL or not TOKEN:
    print("Set SERVER_URL and STREAM_TOKEN environment variables first.")
    print("Example (PowerShell):")
    print('  $env:SERVER_URL="wss://your-app.onrender.com"')
    print('  $env:STREAM_TOKEN="your-secret-token"')
    print("  python agent.py")
    sys.exit(1)

FRAME_INTERVAL = max(1.0 / FPS, 0.05)


def ws_url():
    base = SERVER_URL.rstrip("/")
    return f"{base}/?role=agent&token={quote(TOKEN)}"


def grab_jpeg(sct, monitor):
    """Capture the screen and return a compressed JPEG as bytes."""
    shot = sct.grab(monitor)
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=QUALITY)
    return buf.getvalue()


def stream_once():
    """Connect and stream until the socket drops; raises on error."""
    ws = websocket.create_connection(ws_url(), enable_multithread=True)
    print(f"streaming at ~{FPS} fps (width {MAX_WIDTH}, quality {QUALITY}). Ctrl+C to stop.")
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # primary monitor; use [0] for all monitors
        while True:
            start = time.time()
            frame = grab_jpeg(sct, monitor)
            ws.send_binary(frame)
            elapsed = time.time() - start
            time.sleep(max(0.0, FRAME_INTERVAL - elapsed))


def main():
    print(f"connecting to {SERVER_URL} …")
    while True:
        try:
            stream_once()
        except KeyboardInterrupt:
            print("\nstopped.")
            return
        except Exception as err:  # noqa: BLE001
            print(f"disconnected ({err}). retrying in 3s…")
            time.sleep(3)


if __name__ == "__main__":
    main()
