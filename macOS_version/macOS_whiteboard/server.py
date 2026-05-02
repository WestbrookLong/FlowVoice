import argparse
import asyncio
import ctypes
import ctypes.util
import json
import re
import secrets
import socket
import subprocess
import sys
from collections import deque
from io import BytesIO
from pathlib import Path
from typing import Any

from aiohttp import web
from PIL import ImageGrab


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
TOKEN_FILE = ROOT / ".whiteboard_token"

if sys.platform != "darwin":
    raise SystemExit("This server injects macOS mouse input and must run on macOS.")

APPLICATION_SERVICES = ctypes.CDLL(
    ctypes.util.find_library("ApplicationServices")
    or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
CORE_FOUNDATION = ctypes.CDLL(
    ctypes.util.find_library("CoreFoundation")
    or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)

CGEventRef = ctypes.c_void_p
CGDirectDisplayID = ctypes.c_uint32


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


kCGHIDEventTap = 0
kCGEventMouseMoved = 5
kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGMouseButtonLeft = 0
kCGErrorSuccess = 0
kCGMaxDisplays = 32

APPLICATION_SERVICES.CGEventCreateMouseEvent.argtypes = (
    ctypes.c_void_p,
    ctypes.c_uint32,
    CGPoint,
    ctypes.c_uint32,
)
APPLICATION_SERVICES.CGEventCreateMouseEvent.restype = CGEventRef
APPLICATION_SERVICES.CGEventPost.argtypes = (ctypes.c_uint32, CGEventRef)
APPLICATION_SERVICES.CGEventPost.restype = None
APPLICATION_SERVICES.CGGetActiveDisplayList.argtypes = (
    ctypes.c_uint32,
    ctypes.POINTER(CGDirectDisplayID),
    ctypes.POINTER(ctypes.c_uint32),
)
APPLICATION_SERVICES.CGGetActiveDisplayList.restype = ctypes.c_int32
APPLICATION_SERVICES.CGDisplayBounds.argtypes = (CGDirectDisplayID,)
APPLICATION_SERVICES.CGDisplayBounds.restype = CGRect
APPLICATION_SERVICES.CGMainDisplayID.argtypes = ()
APPLICATION_SERVICES.CGMainDisplayID.restype = CGDirectDisplayID
APPLICATION_SERVICES.CGWarpMouseCursorPosition.argtypes = (CGPoint,)
APPLICATION_SERVICES.CGWarpMouseCursorPosition.restype = ctypes.c_int32
CORE_FOUNDATION.CFRelease.argtypes = (ctypes.c_void_p,)
CORE_FOUNDATION.CFRelease.restype = None


def _point(x: int, y: int) -> CGPoint:
    return CGPoint(float(x), float(y))


def _post_mouse_event(event_type: int, x: int, y: int) -> None:
    event = APPLICATION_SERVICES.CGEventCreateMouseEvent(None, event_type, _point(x, y), kCGMouseButtonLeft)
    if not event:
        raise RuntimeError("failed to create macOS mouse event")
    try:
        APPLICATION_SERVICES.CGEventPost(kCGHIDEventTap, event)
    finally:
        CORE_FOUNDATION.CFRelease(event)


def move_mouse_to_screen_point(x: int, y: int) -> None:
    point = _point(x, y)
    result = APPLICATION_SERVICES.CGWarpMouseCursorPosition(point)
    if result != kCGErrorSuccess:
        raise RuntimeError(f"failed to move macOS cursor: CGError {result}")
    _post_mouse_event(kCGEventMouseMoved, x, y)


def left_mouse_down(x: int, y: int) -> None:
    _post_mouse_event(kCGEventLeftMouseDown, x, y)


def left_mouse_up(x: int, y: int) -> None:
    _post_mouse_event(kCGEventLeftMouseUp, x, y)


def get_lan_ip() -> str:
    try:
        output = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL)
        candidates: list[tuple[int, str]] = []
        current_interface = ""
        ignored_prefixes = ("lo", "utun", "awdl", "llw", "bridge", "gif", "stf")
        for line in output.splitlines():
            header = re.match(r"^([a-zA-Z0-9]+):", line)
            if header:
                current_interface = header.group(1)
                continue
            match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)\b", line)
            if not match or current_interface.startswith(ignored_prefixes):
                continue
            ip = match.group(1)
            if ip.startswith("127."):
                continue
            priority = 0
            if current_interface == "en0":
                priority = 100
            elif current_interface.startswith("en"):
                priority = 80
            elif ip.startswith(("192.168.", "10.", "172.")):
                priority = 60
            candidates.append((priority, ip))
        if candidates:
            return max(candidates)[1]
    except Exception:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def get_or_create_token(requested_token: str | None, reset_token: bool = False) -> str:
    if requested_token:
        TOKEN_FILE.write_text(requested_token, encoding="utf-8")
        return requested_token
    if not reset_token and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(12)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


def _display_bounds(display_id: int) -> dict[str, int]:
    bounds = APPLICATION_SERVICES.CGDisplayBounds(display_id)
    return {
        "left": int(round(bounds.origin.x)),
        "top": int(round(bounds.origin.y)),
        "width": int(round(bounds.size.width)),
        "height": int(round(bounds.size.height)),
    }


def virtual_screen_rect() -> dict[str, int]:
    displays = (CGDirectDisplayID * kCGMaxDisplays)()
    count = ctypes.c_uint32(0)
    result = APPLICATION_SERVICES.CGGetActiveDisplayList(kCGMaxDisplays, displays, ctypes.byref(count))
    if result != kCGErrorSuccess or count.value == 0:
        return _display_bounds(APPLICATION_SERVICES.CGMainDisplayID())

    rects = [_display_bounds(displays[index]) for index in range(count.value)]
    left = min(rect["left"] for rect in rects)
    top = min(rect["top"] for rect in rects)
    right = max(rect["left"] + rect["width"] for rect in rects)
    bottom = max(rect["top"] + rect["height"] for rect in rects)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def monitor_rect(monitor_id: int) -> dict[str, int]:
    displays = (CGDirectDisplayID * kCGMaxDisplays)()
    count = ctypes.c_uint32(0)
    result = APPLICATION_SERVICES.CGGetActiveDisplayList(kCGMaxDisplays, displays, ctypes.byref(count))
    if result != kCGErrorSuccess or count.value == 0:
        return virtual_screen_rect()
    index = max(0, min(monitor_id - 1, count.value - 1))
    return _display_bounds(displays[index])


def capture_monitor_jpeg(monitor: dict[str, int], quality: int = 72) -> bytes:
    bbox = (
        monitor["left"],
        monitor["top"],
        monitor["left"] + monitor["width"],
        monitor["top"] + monitor["height"],
    )
    image = ImageGrab.grab(bbox=bbox, all_screens=True)
    if image.mode != "RGB":
        image = image.convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=max(35, min(90, quality)), optimize=False)
    return buffer.getvalue()


def clamp_ratio(value: Any) -> float:
    return max(0.0, min(1.0, float(value)))


def inject_pointer_event(event: dict[str, Any], monitor: dict[str, int]) -> tuple[int, int]:
    action = event.get("action")
    x_ratio = clamp_ratio(event.get("x"))
    y_ratio = clamp_ratio(event.get("y"))
    x = int(monitor["left"] + x_ratio * max(1, monitor["width"] - 1))
    y = int(monitor["top"] + y_ratio * max(1, monitor["height"] - 1))

    if action == "down":
        move_mouse_to_screen_point(x, y)
        left_mouse_down(x, y)
    elif action == "move":
        move_mouse_to_screen_point(x, y)
    elif action == "up":
        move_mouse_to_screen_point(x, y)
        left_mouse_up(x, y)
    else:
        raise ValueError(f"unsupported pointer action: {action}")
    return x, y


def parse_pointer_message(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    if payload.get("type") == "pointer":
        return [payload]
    if payload.get("type") == "pointer_batch":
        events = payload.get("events")
        if not isinstance(events, list):
            raise ValueError("pointer_batch.events must be a list")
        return [event for event in events[:256] if isinstance(event, dict)]
    raise ValueError("unsupported message type")


class MovePacer:
    def __init__(self, monitor: dict[str, int], interval_seconds: float = 0.002, max_queue: int = 4096) -> None:
        self.monitor = monitor
        self.interval_seconds = interval_seconds
        self.max_queue = max_queue
        self.queue: deque[dict[str, Any]] = deque()
        self.wake = asyncio.Event()
        self.idle = asyncio.Event()
        self.idle.set()
        self.closed = False
        self.task = asyncio.create_task(self._run())

    def enqueue(self, event: dict[str, Any]) -> None:
        if self.closed:
            return
        if len(self.queue) >= self.max_queue:
            self.queue.popleft()
        self.queue.append(event)
        self.idle.clear()
        self.wake.set()

    async def flush(self) -> None:
        if self.closed:
            return
        self.wake.set()
        await self.idle.wait()

    async def close(self) -> None:
        self.closed = True
        self.queue.clear()
        self.wake.set()
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            if not self.queue:
                self.idle.set()
                self.wake.clear()
                await self.wake.wait()
                if self.closed:
                    return
                continue

            self.idle.clear()
            event = self.queue.popleft()
            try:
                inject_pointer_event(event, self.monitor)
            except Exception as exc:
                print(f"[whiteboard move pacer error] {exc}", flush=True)
            await asyncio.sleep(self.interval_seconds)


def no_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def no_cache_file(path: Path) -> web.FileResponse:
    return web.FileResponse(path, headers=no_cache_headers())


def invalid_link_response(lan_ip: str, port: int) -> web.Response:
    body = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Whiteboard Link Expired</title>
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #fff;
        color: #111;
      }}
      main {{
        max-width: 560px;
        padding: 28px;
        line-height: 1.45;
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: 24px;
      }}
      p {{
        margin: 8px 0;
        color: #444;
      }}
      code {{
        color: #111;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Whiteboard link expired</h1>
      <p>This page opened, but its token is missing or no longer matches the Mac service.</p>
      <p>Use the exact URL printed in the Mac window. It should look like:</p>
      <p><code>http://{lan_ip}:{port}/?token=...</code></p>
      <p>If the Mac window was already open before the latest update, close it and start <code>start_desktop_client.sh</code> again.</p>
    </main>
  </body>
</html>"""
    return web.Response(text=body, content_type="text/html", headers=no_cache_headers(), status=401)


async def index(request: web.Request) -> web.StreamResponse:
    app = request.app
    if request.query.get("token") != app["token"]:
        return invalid_link_response(app["lan_ip"], app["port"])
    return no_cache_file(STATIC_DIR / "index.html")


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def snapshot(request: web.Request) -> web.Response:
    app = request.app
    if request.query.get("token") != app["token"]:
        raise web.HTTPUnauthorized(text="invalid token")
    quality = int(request.query.get("quality", "72"))
    frame = await asyncio.to_thread(capture_monitor_jpeg, app["monitor_rect"], quality)
    return web.Response(
        body=frame,
        content_type="image/jpeg",
        headers={
            **no_cache_headers(),
            "X-Monitor-Width": str(app["monitor_rect"]["width"]),
            "X-Monitor-Height": str(app["monitor_rect"]["height"]),
        },
    )


async def screen(request: web.Request) -> web.WebSocketResponse:
    app = request.app
    if request.query.get("token") != app["token"]:
        raise web.HTTPUnauthorized(text="invalid token")

    fps = max(1, min(int(request.query.get("fps", "12")), 24))
    quality = max(30, min(int(request.query.get("quality", "58")), 85))
    interval = 1 / fps

    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=16 * 1024 * 1024)
    await ws.prepare(request)
    peer = request.remote or "unknown"
    print(f"[whiteboard screen] connected: {peer}", flush=True)
    try:
        await ws.send_json({"type": "screen_meta", "monitor": app["monitor_rect"], "fps": fps, "quality": quality})
        while not ws.closed:
            started = asyncio.get_running_loop().time()
            frame = await asyncio.to_thread(capture_monitor_jpeg, app["monitor_rect"], quality)
            await ws.send_bytes(frame)
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.001, interval - elapsed))
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    except Exception as exc:
        print(f"[whiteboard screen error] {exc}", flush=True)
    finally:
        print(f"[whiteboard screen] disconnected: {peer}", flush=True)
    return ws


async def pointer(request: web.Request) -> web.WebSocketResponse:
    app = request.app
    if request.query.get("token") != app["token"]:
        raise web.HTTPUnauthorized(text="invalid token")

    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2 * 1024 * 1024)
    await ws.prepare(request)
    monitor = app["monitor_rect"]
    await ws.send_json({"type": "ready", "monitor": monitor})

    peer = request.remote or "unknown"
    mouse_is_down = False
    last_position = (monitor["left"], monitor["top"])
    move_pacer = MovePacer(monitor)
    print(f"[whiteboard pointer] connected: {peer}", flush=True)
    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                events = parse_pointer_message(msg.data)
                for event in events:
                    action = event.get("action")
                    if action == "move":
                        move_pacer.enqueue(event)
                        last_position = (
                            int(monitor["left"] + clamp_ratio(event.get("x")) * max(1, monitor["width"] - 1)),
                            int(monitor["top"] + clamp_ratio(event.get("y")) * max(1, monitor["height"] - 1)),
                        )
                        continue
                    await move_pacer.flush()
                    if action == "down" and mouse_is_down:
                        left_mouse_up(*last_position)
                        mouse_is_down = False
                    last_position = inject_pointer_event(event, monitor)
                    if action == "down":
                        mouse_is_down = True
                    elif action == "up":
                        mouse_is_down = False
            except Exception as exc:
                print(f"[whiteboard pointer error] {exc}", flush=True)
                await ws.send_json({"type": "error", "message": str(exc)})
    finally:
        await move_pacer.close()
        if mouse_is_down:
            try:
                left_mouse_up(*last_position)
            except Exception as exc:
                print(f"[whiteboard pointer cleanup error] {exc}", flush=True)
        print(f"[whiteboard pointer] disconnected: {peer}", flush=True)
    return ws


def create_app(token: str, monitor_id: int, lan_ip: str | None = None, port: int | None = None) -> web.Application:
    app = web.Application()
    app["token"] = token
    app["monitor_rect"] = monitor_rect(monitor_id)
    app["lan_ip"] = lan_ip or get_lan_ip()
    app["port"] = port or 8791
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/snapshot", snapshot)
    app.router.add_get("/screen", screen)
    app.router.add_get("/pointer", pointer)
    app.router.add_static("/static", STATIC_DIR)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local iPad whiteboard to macOS mouse bridge.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--token", default=None)
    parser.add_argument("--new-token", action="store_true", help="Generate and persist a new token.")
    parser.add_argument("--monitor", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = get_or_create_token(args.token, reset_token=args.new_token)
    app = create_app(token=token, monitor_id=args.monitor, lan_ip=get_lan_ip(), port=args.port)
    lan_ip = get_lan_ip()
    print("iPad whiteboard bridge for macOS is running.", flush=True)
    print(f"Browser whiteboard: http://{lan_ip}:{args.port}/?token={token}", flush=True)
    print(f"iPad app connect URL: http://{lan_ip}:{args.port}/?token={token}", flush=True)
    print(f"Mapping target: monitor={args.monitor}, rect={app['monitor_rect']}", flush=True)
    print("Switch tools in the target Mac drawing app manually; this bridge only maps pointer strokes.", flush=True)
    print("macOS may require Accessibility permission for Terminal or Python.", flush=True)
    try:
        web.run_app(app, host=args.host, port=args.port, print=None)
    except OSError as exc:
        print("", flush=True)
        print(f"Failed to start server on port {args.port}.", flush=True)
        print(f"Reason: {exc}", flush=True)
        print("If this port is already in use, close the old window or start with another port:", flush=True)
        print(f"  python3 server.py --port {args.port + 1}", flush=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
