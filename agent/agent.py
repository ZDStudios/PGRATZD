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

import ctypes

SERVER_URL  = os.environ.get("SERVER_URL",  "wss://pgratzd.onrender.com")
TOKEN       = os.environ.get("STREAM_TOKEN", "changeme")
FPS         = float(os.environ.get("FPS",       "4"))
MAX_WIDTH   = int(os.environ.get("MAX_WIDTH",   "1280"))
QUALITY     = int(os.environ.get("QUALITY",     "60"))
DEVICE_NAME = os.environ.get("DEVICE_NAME", socket.gethostname())
MAX_DOWNLOAD = 50 * 1024 * 1024  # 50 MB

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

        extra = {}
        if platform.system() == "Windows":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **extra,
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


# ── Apps (Windows) ─────────────────────────────────────────────────

def _extract_icon_b64(path, size=48):
    """Extract icon via pure ctypes (no pywin32 needed — works in PyInstaller bundles)."""
    if platform.system() != "Windows":
        return None
    try:
        shell32 = ctypes.windll.shell32
        user32  = ctypes.windll.user32
        gdi32   = ctypes.windll.gdi32

        # Set 64-bit-safe return types for handle-returning functions
        for fn in (user32.GetDC, gdi32.CreateCompatibleDC, gdi32.CreateCompatibleBitmap,
                   gdi32.SelectObject, gdi32.CreateSolidBrush):
            fn.restype = ctypes.c_size_t
        shell32.SHGetFileInfoW.restype = ctypes.c_size_t

        class SHFILEINFOW(ctypes.Structure):
            _fields_ = [
                ("hIcon",         ctypes.c_size_t),
                ("iIcon",         ctypes.c_int),
                ("dwAttributes",  ctypes.c_uint32),
                ("szDisplayName", ctypes.c_wchar * 260),
                ("szTypeName",    ctypes.c_wchar * 80),
            ]

        shell32.ExtractIconExW.restype = ctypes.c_uint

        # Primary: SHGetFileInfoW (resolves .lnk via shell, needs COM on thread)
        shfi = SHFILEINFOW()
        ret  = shell32.SHGetFileInfoW(
            str(path), 0, ctypes.byref(shfi), ctypes.sizeof(shfi),
            0x100 | 0x0,  # SHGFI_ICON | SHGFI_LARGEICON
        )
        hicon = shfi.hIcon if (ret and shfi.hIcon) else 0

        # Fallback: ExtractIconExW (works without COM for plain EXE/DLL icons)
        if not hicon:
            hL = ctypes.c_size_t(0)
            hS = ctypes.c_size_t(0)
            if shell32.ExtractIconExW(str(path), 0, ctypes.byref(hL), ctypes.byref(hS), 1):
                hicon = hL.value or hS.value
                unused = hS.value if hicon == hL.value else hL.value
                if unused:
                    user32.DestroyIcon(unused)

        if not hicon:
            return None

        # Create memory DC + bitmap to render the icon into
        hdc_screen = user32.GetDC(None)
        hdc_mem    = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp       = gdi32.CreateCompatibleBitmap(hdc_screen, size, size)
        gdi32.SelectObject(hdc_mem, hbmp)

        # Fill background (#1a1e25 → COLORREF = 0x00251e1a)
        class RECT(ctypes.Structure):
            _fields_ = [("left",ctypes.c_long),("top",ctypes.c_long),
                        ("right",ctypes.c_long),("bottom",ctypes.c_long)]
        hbr = gdi32.CreateSolidBrush(0x00251e1a)
        rc  = RECT(0, 0, size, size)
        user32.FillRect(hdc_mem, ctypes.byref(rc), hbr)
        gdi32.DeleteObject(hbr)

        # Draw icon: DI_NORMAL = 0x3
        user32.DrawIconEx(hdc_mem, 0, 0, hicon, size, size, 0, None, 0x3)
        user32.DestroyIcon(hicon)

        # Read pixels back as a 32-bit top-down DIB
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize",          ctypes.c_uint32),
                ("biWidth",         ctypes.c_int32),
                ("biHeight",        ctypes.c_int32),
                ("biPlanes",        ctypes.c_uint16),
                ("biBitCount",      ctypes.c_uint16),
                ("biCompression",   ctypes.c_uint32),
                ("biSizeImage",     ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed",       ctypes.c_uint32),
                ("biClrImportant",  ctypes.c_uint32),
            ]
        bih = BITMAPINFOHEADER(
            biSize=ctypes.sizeof(BITMAPINFOHEADER),
            biWidth=size, biHeight=-size,  # negative = top-down
            biPlanes=1, biBitCount=32, biCompression=0,
        )
        pixel_buf = (ctypes.c_char * (size * size * 4))()
        rows = gdi32.GetDIBits(hdc_mem, hbmp, 0, size, pixel_buf, ctypes.byref(bih), 0)

        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)
        gdi32.DeleteObject(hbmp)

        if not rows:
            return None

        img = Image.frombuffer("RGB", (size, size), bytes(pixel_buf), "raw", "BGRX", 0, 1)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _apps_work(ws_conn, msg):
    op = msg.get("op")
    req_id = msg.get("id")

    def reply(**data):
        try:
            ws_conn.send(json.dumps({"type": "apps_res", "id": req_id, "op": op, **data}))
        except Exception:
            pass

    if op == "list":
        # COM must be initialized on this thread for shell icon resolution
        # (needed for Office, UWP, and other COM-based icon handlers)
        ctypes.windll.ole32.CoInitialize(None)
        try:
            roots = []
            for base_env in ["PROGRAMDATA", "APPDATA"]:
                base = os.environ.get(base_env, "")
                if base:
                    p = pathlib.Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                    if p.exists():
                        roots.append(p)

            seen: set = set()
            apps = []
            for root in roots:
                for lnk in sorted(root.rglob("*.lnk"), key=lambda x: x.stem.lower()):
                    key = lnk.stem.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    rel = lnk.relative_to(root)
                    category = str(rel.parent) if str(rel.parent) != "." else ""
                    icon = _extract_icon_b64(str(lnk))
                    apps.append({
                        "name": lnk.stem,
                        "path": str(lnk),
                        "category": category,
                        "icon": icon,
                    })
        finally:
            ctypes.windll.ole32.CoUninitialize()

        reply(apps=sorted(apps, key=lambda a: a["name"].lower()))

    elif op == "launch":
        path = msg.get("path", "")
        try:
            os.startfile(path)
            reply(success=True)
        except Exception as exc:
            reply(error=str(exc))


def handle_apps_req(ws_conn, msg):
    threading.Thread(target=_apps_work, args=(ws_conn, msg), daemon=True).start()


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
            elif t == "apps_req":
                handle_apps_req(ws_conn, msg)
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


def _agent_main():
    """Core agent loop: stream screen and handle remote commands."""
    while True:
        try:
            stream_once()
        except KeyboardInterrupt:
            return
        except Exception:
            time.sleep(3)


# ── Watchdog / self-restart ────────────────────────────────────────

def _spawn(extra_args):
    """Spawn another instance of this process silently."""
    if getattr(sys, "frozen", False):
        cmd = [sys.argv[0]] + extra_args
    else:
        cmd = [sys.executable, sys.argv[0]] + extra_args
    return subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def _wait_pid(pid):
    """Block until the given PID exits (Windows only)."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if handle:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)   # INFINITE
        kernel32.CloseHandle(handle)


def _guard_loop():
    """Guard process: if the main agent dies, restart it."""
    try:
        target_pid = int(sys.argv[sys.argv.index("--guard") + 1])
    except (ValueError, IndexError):
        return
    while True:
        _wait_pid(target_pid)
        time.sleep(2)
        try:
            proc = _spawn(["--guarded"])
            target_pid = proc.pid
        except Exception:
            time.sleep(5)


if __name__ == "__main__":
    if "--guard" in sys.argv:
        # Running as the silent watchdog
        _guard_loop()
    else:
        if "--guarded" not in sys.argv:
            # First launch: start the guard that will restart us if we die
            try:
                _spawn(["--guard", str(os.getpid())])
            except Exception:
                pass
        _agent_main()
