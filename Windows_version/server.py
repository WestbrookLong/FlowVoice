import argparse
import ctypes
import json
import logging
import os
import secrets
import socket
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

from aiohttp import web


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


ROOT = app_root()
STATIC_DIR = ROOT / "static"
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FlowBridge"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "flowbridge.log"

LOGGER = logging.getLogger("flowbridge")
LOGGER.setLevel(logging.INFO)
LOGGER.handlers.clear()
file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
LOGGER.addHandler(file_handler)
if sys.stdout is not None:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(stream_handler)


def log(message: str) -> None:
    LOGGER.info(message)

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2
VK_BACK = 0x08
VK_RETURN = 0x0D
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
USER32 = ctypes.WinDLL("user32", use_last_error=True)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    # Keep the full Win32 INPUT union layout. SendInput validates cbSize against
    # the platform ABI even when this program only sends keyboard events.
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


USER32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
USER32.SendInput.restype = wintypes.UINT


def _send_keyboard_input(vk: int = 0, scan: int = 0, flags: int = 0) -> None:
    event = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(
            ki=KEYBDINPUT(
                wVk=vk,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    sent = USER32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
    if sent != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def press_key(vk: int) -> None:
    _send_keyboard_input(vk=vk)
    _send_keyboard_input(vk=vk, flags=KEYEVENTF_KEYUP)


def type_text(text: str) -> None:
    # SendInput with KEYEVENTF_UNICODE accepts UTF-16 code units, preserving Chinese and emoji.
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        code_unit = data[i] | (data[i + 1] << 8)
        if code_unit == 0x000A:
            press_key(VK_RETURN)
            continue
        if code_unit == 0x000D:
            continue
        _send_keyboard_input(scan=code_unit, flags=KEYEVENTF_UNICODE)
        _send_keyboard_input(scan=code_unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)


def backspace(count: int) -> None:
    for _ in range(max(0, min(count, 1000))):
        press_key(VK_BACK)


class TextSession:
    def __init__(self) -> None:
        self.text = ""

    def replace(self, new_text: str) -> None:
        prefix_len = common_prefix_len(self.text, new_text)
        delete_count = len(list(self.text[prefix_len:]))
        insert_text = new_text[prefix_len:]
        if delete_count:
            backspace(delete_count)
        if insert_text:
            type_text(insert_text)
        self.text = new_text

    def reset(self) -> None:
        self.text = ""


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def common_prefix_len(left: str, right: str) -> int:
    length = min(len(left), len(right))
    index = 0
    while index < length and left[index] == right[index]:
        index += 1
    return index


def validate_ops(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("type") != "ops":
        raise ValueError("unsupported message type")
    ops = payload.get("ops")
    if not isinstance(ops, list):
        raise ValueError("ops must be a list")
    normalized: list[dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            raise ValueError("op must be an object")
        op_type = op.get("type")
        if op_type == "insert":
            text = op.get("text")
            if not isinstance(text, str):
                raise ValueError("insert.text must be a string")
            if text:
                normalized.append({"type": "insert", "text": text[:5000]})
        elif op_type == "enter":
            normalized.append({"type": "enter"})
        elif op_type == "backspace":
            count = op.get("count")
            if not isinstance(count, int):
                raise ValueError("backspace.count must be an integer")
            if count > 0:
                normalized.append({"type": "backspace", "count": min(count, 1000)})
        else:
            raise ValueError(f"unsupported op type: {op_type}")
    return normalized


def create_app(token: str) -> web.Application:
    app = web.Application()
    session = TextSession()

    def html_response(path: Path) -> web.FileResponse:
        return web.FileResponse(
            path,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    async def index(_: web.Request) -> web.FileResponse:
        return html_response(STATIC_DIR / "index.html")

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        if request.query.get("token") != token:
            raise web.HTTPUnauthorized(text="invalid token")

        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        peer = request.remote or "unknown"
        log(f"[ws] connected: {peer}")
        await ws.send_json({"type": "ready"})

        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
                if payload.get("token") != token:
                    raise ValueError("invalid token")
                if payload.get("type") == "sync_text":
                    text = payload.get("text")
                    if not isinstance(text, str):
                        raise ValueError("sync_text.text must be a string")
                    text = text[:5000]
                    log(
                        f"[sync] replace {len(session.text)} chars -> {len(text)} chars: {text!r}",
                    )
                    session.replace(text)
                elif payload.get("type") == "reset_session":
                    log("[sync] reset session")
                    session.reset()
                else:
                    ops = validate_ops(payload)
                    for op in ops:
                        if op["type"] == "insert":
                            log(f"[inject] insert {len(op['text'])} chars: {op['text']!r}")
                            type_text(op["text"])
                        elif op["type"] == "enter":
                            log("[inject] enter")
                            press_key(VK_RETURN)
                        elif op["type"] == "backspace":
                            log(f"[inject] backspace {op['count']}")
                            backspace(op["count"])
                await ws.send_json({"type": "ack", "seq": payload.get("seq")})
            except Exception as exc:
                log(f"[error] {exc}")
                await ws.send_json({"type": "error", "message": str(exc)})

        log(f"[ws] disconnected: {peer}")
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static", STATIC_DIR)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phone realtime input bridge for Windows.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host, default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8787, help="Bind port, default: 8787")
    parser.add_argument("--token", default=None, help="Session token. Defaults to a random token.")
    parser.add_argument("--test-text", default=None, help="Type text into the current Windows focus after a countdown, then exit.")
    return parser.parse_args()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This program injects text with Windows SendInput and must run on Windows.")

    args = parse_args()
    if args.test_text is not None:
        log("Focus the target input box within 3 seconds...")
        import time

        time.sleep(3)
        type_text(args.test_text)
        log("Test text sent.")
        return

    token = args.token or secrets.token_urlsafe(12)
    app = create_app(token)

    lan_ip = get_lan_ip()
    log("Phone realtime input bridge is running.")
    log(f"Open on phone: http://{lan_ip}:{args.port}/?token={token}")
    log("Keep the target app focused on this PC; phone input will be typed there.")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
