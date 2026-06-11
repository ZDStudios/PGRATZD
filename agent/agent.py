"""Screen-capture + remote-control + file-manager agent.

Environment variables:
  SERVER_URL    wss://your-app.onrender.com   (required)
  STREAM_TOKEN  secret token                   (default: changeme)
  DEVICE_NAME   name shown in dashboard        (default: hostname)
  FPS           frames per second              (default 4)
  MAX_WIDTH     downscale width in px          (default 1280)
  QUALITY       JPEG quality 1-100             (default 60)

Install: pip install -r requirements.txt
"""

import base64
import io
import json
import os
import pathlib
import platform
import shutil
import socket
import subprocess
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
MAX_DOWNLOAD = 50 * 1024 * 1024  # 50 MB

if not SERVER_URL:
    print("Set SERVER_URL environment variable first.")
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


# ── Control ────────────────────────────────────────────────────────

def handle_control(msg):
    ev = msg.get("event")
    sw, sh = pyautogui.size()
    try:
        if ev == "mousemove":
            pyautogui.moveTo(int(msg["x"] * sw), int(msg["y"] * sh))
        elif ev == "mousedown":
            b = msg.get("button", 0)
            pyautogui.mouseDown(button=BUTTONS[b] if isinstance(b, int) else b)
        elif ev == "mouseup":
            b = msg.get("button", 0)
            pyautogui.mouseUp(button=BUTTONS[b] if isinstance(b, int) else b)
        elif ev == "scroll":
            dy = msg.get("dy", 0)
            divisor = 1 if msg.get("mode", 0) == 1 else 100
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


# ── File system ────────────────────────────────────────────────────

def _fs_work(ws_conn, msg):
    op = msg.get("op")
    req_id = msg.get("id")

    def reply(**data):
        ws_conn.send(json.dumps({"type": "fs_res", "id": req_id, "op": op, **data}))

    def err(text):
        reply(error=text)

    try:
        raw = msg.get("path") or ""
        path = pathlib.Path(raw).resolve() if raw else pathlib.Path.home()

        if op == "list":
            entries = []
            try:
                items = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                err("Permission denied")
                return
            for entry in items:
                try:
                    st = entry.stat()
                    entries.append({
                        "name": entry.name,
                        "path": str(entry),
                        "isDir": entry.is_dir(),
                        "size": None if entry.is_dir() else st.st_size,
                        "modified": int(st.st_mtime * 1000),
                    })
                except OSError:
                    entries.append({"name": entry.name, "path": str(entry),
                                    "isDir": entry.is_dir(), "size": None, "modified": None})
            parent = str(path.parent) if path != path.parent else None
            reply(path=str(path), parent=parent, entries=entries)

        elif op == "download":
            if not path.is_file():
                err("Not a file")
                return
            size = path.stat().st_size
            if size > MAX_DOWNLOAD:
                err(f"File too large ({size // (1024*1024)} MB). Max 50 MB.")
                return
            content = base64.b64encode(path.read_bytes()).decode()
            reply(path=str(path), content=content, size=size)

        elif op == "upload":
            data_b64 = msg.get("content", "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(data_b64))
            reply(path=str(path), success=True)

        elif op == "mkdir":
            path.mkdir(parents=True, exist_ok=True)
            reply(path=str(path), success=True)

        elif op == "mkfile":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            reply(path=str(path), success=True)

        elif op == "delete":
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            reply(path=str(path), success=True)

        elif op == "rename":
            new_path = path.parent / msg["newName"]
            path.rename(new_path)
            reply(path=str(path), newPath=str(new_path), success=True)

        else:
            err(f"Unknown op: {op}")

    except Exception as exc:
        try:
            ws_conn.send(json.dumps({"type": "fs_res", "id": req_id, "op": op, "error": str(exc)}))
        except Exception:
            pass


def handle_fs_req(ws_conn, msg):
    threading.Thread(target=_fs_work, args=(ws_conn, msg), daemon=True).start()


# ── Command execution ──────────────────────────────────────────────

def _exec_work(ws_conn, msg):
    req_id = msg.get("id")
    command = (msg.get("command") or "").strip()

    def send(data):
        try:
            ws_conn.send(json.dumps({"type": "exec_res", "id": req_id, **data}))
        except Exception:
            pass

    if not command:
        send({"done": True, "exitCode": 0})
        return

    try:
        if platform.system() == "Windows":
            # Prepend UTF-8 setup so output is readable
            ps_cmd = f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; {command}"
            args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd]
        else:
            args = ["bash", "-c", command]

        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        stdout_done = threading.Event()
        stderr_done = threading.Event()

        def pipe_reader(stream, stream_name, done_evt):
            try:
                for line in stream:
                    send({"output": line, "stream": stream_name})
            except Exception:
                pass
            finally:
                done_evt.set()

        threading.Thread(target=pipe_reader, args=(proc.stdout, "stdout", stdout_done), daemon=True).start()
        threading.Thread(target=pipe_reader, args=(proc.stderr, "stderr", stderr_done), daemon=True).start()

        # Wait up to 60 s for output to finish
        stdout_done.wait(timeout=60)
        stderr_done.wait(timeout=5)

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        send({"done": True, "exitCode": proc.returncode if proc.returncode is not None else 0})

    except Exception as exc:
        send({"output": f"Error: {exc}\n", "stream": "stderr", "done": True, "exitCode": -1})


def handle_exec_req(ws_conn, msg):
    threading.Thread(target=_exec_work, args=(ws_conn, msg), daemon=True).start()


# ── WebSocket receive loop ─────────────────────────────────────────

def recv_loop(ws_conn):
    while True:
        try:
            data = ws_conn.recv()
            if not isinstance(data, str):
                continue
            msg = json.loads(data)
            t = msg.get("type")
            if t == "control":
                handle_control(msg)
            elif t == "fs_req":
                handle_fs_req(ws_conn, msg)
            elif t == "exec_req":
                handle_exec_req(ws_conn, msg)
        except (json.JSONDecodeError, KeyError):
            pass
        except Exception:
            break


# ── Streaming ──────────────────────────────────────────────────────

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
    ws_conn = websocket.create_connection(ws_url(), enable_multithread=True)
    print(f"streaming as '{DEVICE_NAME}' at ~{FPS} fps (width {MAX_WIDTH}, quality {QUALITY}). Ctrl+C to stop.")
    threading.Thread(target=recv_loop, args=(ws_conn,), daemon=True).start()
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        while True:
            start = time.time()
            ws_conn.send_binary(grab_jpeg(sct, monitor))
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
