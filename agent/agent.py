"""Local screen-capture + remote-control agent.

Run on your own computer. Streams your screen to the Render dashboard and
optionally accepts mouse/keyboard control events from it.

Environment variables:
  SERVER_URL    wss://your-app.onrender.com   (required)
  STREAM_TOKEN  secret token                   (default: changeme)
  DEVICE_NAME   name shown in dashboard        (default: hostname)
  FPS           frames per second              (default 4)
  MAX_WIDTH     downscale width in px          (default 1280)
  QUALITY       JPEG quality 1-100             (default 60)

Install: pip install -r requirements.txt
"""

import io
import json
import os
import socket
import sys
import threading
import time
from urllib.parse import quote

import mss
import pyautogui
import websocket
from PIL import Image

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

SERVER_URL = os.environ.get("SERVER_URL")
TOKEN = os.environ.get("STREAM_TOKEN", "changeme")
FPS = float(os.environ.get("FPS", "4"))
MAX_WIDTH = int(os.environ.get("MAX_WIDTH", "1280"))
QUALITY = int(os.environ.get("QUALITY", "60"))
DEVICE_NAME = os.environ.get("DEVICE_NAME", socket.gethostname())

if not SERVER_URL:
    print("Set SERVER_URL environment variable first.")
    print("Example (PowerShell):")
    print('  $env:SERVER_URL="wss://your-app.onrender.com"')
    print("  python agent.py")
    sys.exit(1)

FRAME_INTERVAL = max(1.0 / FPS, 0.05)

KEY_MAP = {
    "KeyA": "a", "KeyB": "b", "KeyC": "c", "KeyD": "d", "KeyE": "e",
    "KeyF": "f", "KeyG": "g", "KeyH": "h", "KeyI": "i", "KeyJ": "j",
    "KeyK": "k", "KeyL": "l", "KeyM": "m", "KeyN": "n", "KeyO": "o",
    "KeyP": "p", "KeyQ": "q", "KeyR": "r", "KeyS": "s", "KeyT": "t",
    "KeyU": "u", "KeyV": "v", "KeyW": "w", "KeyX": "x", "KeyY": "y",
    "KeyZ": "z",
    "Digit0": "0", "Digit1": "1", "Digit2": "2", "Digit3": "3", "Digit4": "4",
    "Digit5": "5", "Digit6": "6", "Digit7": "7", "Digit8": "8", "Digit9": "9",
    "Numpad0": "num0", "Numpad1": "num1", "Numpad2": "num2", "Numpad3": "num3",
    "Numpad4": "num4", "Numpad5": "num5", "Numpad6": "num6", "Numpad7": "num7",
    "Numpad8": "num8", "Numpad9": "num9",
    "NumpadAdd": "add", "NumpadSubtract": "subtract", "NumpadMultiply": "multiply",
    "NumpadDivide": "divide", "NumpadDecimal": "decimal", "NumpadEnter": "enter",
    "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4", "F5": "f5", "F6": "f6",
    "F7": "f7", "F8": "f8", "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
    "ArrowLeft": "left", "ArrowRight": "right", "ArrowUp": "up", "ArrowDown": "down",
    "Enter": "enter", "Escape": "esc", "Backspace": "backspace", "Delete": "delete",
    "Tab": "tab", "Space": "space",
    "ShiftLeft": "shiftleft", "ShiftRight": "shiftright",
    "ControlLeft": "ctrlleft", "ControlRight": "ctrlright",
    "AltLeft": "altleft", "AltRight": "altright",
    "MetaLeft": "winleft", "MetaRight": "winright",
    "CapsLock": "capslock", "NumLock": "numlock", "ScrollLock": "scrolllock",
    "Home": "home", "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
    "Insert": "insert", "PrintScreen": "prtsc", "Pause": "pause",
    "Minus": "-", "Equal": "=", "BracketLeft": "[", "BracketRight": "]",
    "Backslash": "\\", "Semicolon": ";", "Quote": "'", "Comma": ",",
    "Period": ".", "Slash": "/", "Backquote": "`",
}

BUTTONS = ["left", "middle", "right"]


def handle_control(data):
    try:
        msg = json.loads(data)
        if msg.get("type") != "control":
            return
        ev = msg.get("event")
        sw, sh = pyautogui.size()

        if ev == "mousemove":
            pyautogui.moveTo(int(msg["x"] * sw), int(msg["y"] * sh))
        elif ev == "mousedown":
            btn = BUTTONS[msg.get("button", 0)] if isinstance(msg.get("button"), int) else msg.get("button", "left")
            pyautogui.mouseDown(button=btn)
        elif ev == "mouseup":
            btn = BUTTONS[msg.get("button", 0)] if isinstance(msg.get("button"), int) else msg.get("button", "left")
            pyautogui.mouseUp(button=btn)
        elif ev == "scroll":
            dy = msg.get("dy", 0)
            mode = msg.get("mode", 0)
            divisor = 1 if mode == 1 else 100
            clicks = -round(dy / divisor) or (-1 if dy > 0 else 1)
            pyautogui.scroll(max(-5, min(5, clicks)))
        elif ev == "keydown":
            key = KEY_MAP.get(msg.get("code", ""))
            if key:
                pyautogui.keyDown(key)
        elif ev == "keyup":
            key = KEY_MAP.get(msg.get("code", ""))
            if key:
                pyautogui.keyUp(key)
    except Exception as err:
        print(f"control error: {err}")


def recv_loop(ws):
    while True:
        try:
            data = ws.recv()
            if isinstance(data, str):
                handle_control(data)
        except Exception:
            break


def ws_url():
    base = SERVER_URL.rstrip("/")
    return f"{base}/?role=agent&token={quote(TOKEN)}&name={quote(DEVICE_NAME)}"


def grab_jpeg(sct, monitor):
    shot = sct.grab(monitor)
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=QUALITY)
    return buf.getvalue()


def stream_once():
    ws = websocket.create_connection(ws_url(), enable_multithread=True)
    print(f"streaming as '{DEVICE_NAME}' at ~{FPS} fps (width {MAX_WIDTH}, quality {QUALITY}). Ctrl+C to stop.")
    threading.Thread(target=recv_loop, args=(ws,), daemon=True).start()
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        while True:
            start = time.time()
            ws.send_binary(grab_jpeg(sct, monitor))
            time.sleep(max(0.0, FRAME_INTERVAL - (time.time() - start)))


def main():
    print(f"connecting to {SERVER_URL} …")
    while True:
        try:
            stream_once()
        except KeyboardInterrupt:
            print("\nstopped.")
            return
        except Exception as err:
            print(f"disconnected ({err}). retrying in 3s…")
            time.sleep(3)


if __name__ == "__main__":
    main()
