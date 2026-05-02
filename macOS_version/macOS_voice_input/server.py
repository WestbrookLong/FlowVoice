import argparse
import ctypes
import ctypes.util
import ipaddress
import json
import secrets
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from aiohttp import web


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

VK_BACK = 0x08
VK_RETURN = 0x0D

APPLICATION_SERVICES = ctypes.CDLL(
    ctypes.util.find_library("ApplicationServices")
    or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
CORE_FOUNDATION = ctypes.CDLL(
    ctypes.util.find_library("CoreFoundation")
    or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)

CGEventRef = ctypes.c_void_p
CGKeyCode = ctypes.c_uint16
UniChar = ctypes.c_uint16
kCGHIDEventTap = 0
MAC_KEY_RETURN = 36
MAC_KEY_DELETE = 51

APPLICATION_SERVICES.CGEventCreateKeyboardEvent.argtypes = (
    ctypes.c_void_p,
    CGKeyCode,
    ctypes.c_bool,
)
APPLICATION_SERVICES.CGEventCreateKeyboardEvent.restype = CGEventRef
APPLICATION_SERVICES.CGEventKeyboardSetUnicodeString.argtypes = (
    CGEventRef,
    ctypes.c_ulong,
    ctypes.POINTER(UniChar),
)
APPLICATION_SERVICES.CGEventPost.argtypes = (ctypes.c_uint32, CGEventRef)
APPLICATION_SERVICES.CGEventPost.restype = None
CORE_FOUNDATION.CFRelease.argtypes = (ctypes.c_void_p,)
CORE_FOUNDATION.CFRelease.restype = None


def _post_mac_key(key_code: int, is_down: bool) -> None:
    event = APPLICATION_SERVICES.CGEventCreateKeyboardEvent(None, key_code, is_down)
    if not event:
        raise RuntimeError("failed to create macOS keyboard event")
    try:
        APPLICATION_SERVICES.CGEventPost(kCGHIDEventTap, event)
    finally:
        CORE_FOUNDATION.CFRelease(event)


def _post_mac_unicode(code_unit: int, is_down: bool) -> None:
    event = APPLICATION_SERVICES.CGEventCreateKeyboardEvent(None, 0, is_down)
    if not event:
        raise RuntimeError("failed to create macOS unicode keyboard event")
    chars = (UniChar * 1)(code_unit)
    try:
        APPLICATION_SERVICES.CGEventKeyboardSetUnicodeString(event, 1, chars)
        APPLICATION_SERVICES.CGEventPost(kCGHIDEventTap, event)
    finally:
        CORE_FOUNDATION.CFRelease(event)


def press_key(vk: int) -> None:
    key_code = MAC_KEY_RETURN if vk == VK_RETURN else MAC_KEY_DELETE if vk == VK_BACK else vk
    _post_mac_key(key_code, True)
    _post_mac_key(key_code, False)


def type_text(text: str) -> None:
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        code_unit = data[i] | (data[i + 1] << 8)
        if code_unit == 0x000A:
            press_key(VK_RETURN)
            continue
        if code_unit == 0x000D:
            continue
        _post_mac_unicode(code_unit, True)
        _post_mac_unicode(code_unit, False)


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


def _is_usable_lan_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and address.is_private and not address.is_loopback


def _get_interface_ip(interface_name: str) -> str | None:
    try:
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    address = result.stdout.strip()
    return address if _is_usable_lan_ip(address) else None


def get_lan_ip() -> str:
    for interface_name in ("en0", "en1", "en2", "en3"):
        address = _get_interface_ip(interface_name)
        if address:
            return address

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            address = s.getsockname()[0]
            return address if _is_usable_lan_ip(address) else "127.0.0.1"
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

    async def index(request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        if request.query.get("token") != token:
            raise web.HTTPUnauthorized(text="invalid token")

        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        peer = request.remote or "unknown"
        print(f"[ws] connected: {peer}", flush=True)
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
                    print(
                        f"[sync] replace {len(session.text)} chars -> {len(text)} chars: {text!r}",
                        flush=True,
                    )
                    session.replace(text)
                elif payload.get("type") == "reset_session":
                    print("[sync] reset session", flush=True)
                    session.reset()
                else:
                    ops = validate_ops(payload)
                    for op in ops:
                        if op["type"] == "insert":
                            print(f"[inject] insert {len(op['text'])} chars: {op['text']!r}", flush=True)
                            type_text(op["text"])
                        elif op["type"] == "enter":
                            print("[inject] enter", flush=True)
                            press_key(VK_RETURN)
                        elif op["type"] == "backspace":
                            print(f"[inject] backspace {op['count']}", flush=True)
                            backspace(op["count"])
                await ws.send_json({"type": "ack", "seq": payload.get("seq")})
            except Exception as exc:
                print(f"[error] {exc}", flush=True)
                await ws.send_json({"type": "error", "message": str(exc)})

        print(f"[ws] disconnected: {peer}", flush=True)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static", STATIC_DIR)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phone realtime input bridge for macOS.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host, default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8787, help="Bind port, default: 8787")
    parser.add_argument("--token", default=None, help="Session token. Defaults to a random token.")
    parser.add_argument("--test-text", default=None, help="Type text into the current Mac focus after a countdown, then exit.")
    return parser.parse_args()


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This macOS version injects text with CoreGraphics and must run on macOS.")

    args = parse_args()
    if args.test_text is not None:
        print("Focus the target input box within 3 seconds...", flush=True)
        import time

        time.sleep(3)
        type_text(args.test_text)
        print("Test text sent.", flush=True)
        return

    token = args.token or secrets.token_urlsafe(12)
    app = create_app(token)

    lan_ip = get_lan_ip()
    print("Phone realtime input bridge for macOS is running.")
    print(f"Open on phone: http://{lan_ip}:{args.port}/?token={token}")
    print("Keep the target app focused on this Mac; phone input will be typed there.")
    print("macOS may require Accessibility and Input Monitoring permission for Terminal or Python.")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
