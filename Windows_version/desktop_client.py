import asyncio
import ctypes
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

from aiohttp import web
import webview

from server import create_app, get_lan_ip


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def ui_index_path() -> Path:
    return app_root() / "desktop_ui" / "dist" / "index.html"


def ui_url() -> str:
    dev_url = os.environ.get("FLOWBRIDGE_UI_DEV_URL")
    if dev_url:
        return dev_url

    index_path = ui_index_path()
    if not index_path.exists():
        raise SystemExit(f"React desktop UI is not built: {index_path}")
    return index_path.as_uri()


def copy_text_to_clipboard(text: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Clipboard copy is only implemented for Windows.")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    if not user32.OpenClipboard(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not user32.EmptyClipboard():
            raise ctypes.WinError(ctypes.get_last_error())

        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        kernel32.GlobalAlloc.argtypes = (ctypes.c_uint, ctypes.c_size_t)
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
        user32.SetClipboardData.argtypes = (ctypes.c_uint, ctypes.c_void_p)
        user32.SetClipboardData.restype = ctypes.c_void_p
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        locked = kernel32.GlobalLock(handle)
        if not locked:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(locked, buffer, size)
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        user32.CloseClipboard()


class BridgeServerThread(threading.Thread):
    def __init__(self, host: str, port: int, token: str) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.token = token
        self.loop: asyncio.AbstractEventLoop | None = None
        self.runner: web.AppRunner | None = None
        self.ready = threading.Event()
        self.error: str | None = None

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._start())
            self.ready.set()
            self.loop.run_forever()
        except Exception as exc:
            self.error = str(exc)
            self.ready.set()
        finally:
            self.loop.run_until_complete(self._cleanup())
            self.loop.close()

    async def _start(self) -> None:
        app = create_app(self.token)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()

    async def _cleanup(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    def stop(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)


class DesktopApi:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.lan_ip = get_lan_ip()
        self.page_version = str(int(time.time()))
        self.token = secrets.token_urlsafe(12)
        self.port = "8787"
        self.server_thread: BridgeServerThread | None = None
        self.window: webview.Window | None = None
        self.maximized = False

    def _url(self) -> str:
        return f"http://{self.lan_ip}:{self.port}/?token={self.token}&v={self.page_version}"

    def _running(self) -> bool:
        return self.server_thread is not None and self.server_thread.error is None

    def _result(self, message: str = "") -> dict:
        return {"state": self.get_state(), "message": message}

    def get_state(self) -> dict:
        with self.lock:
            running = self._running()
            return {
                "running": running,
                "token": self.token,
                "ip": self.lan_ip,
                "port": self.port,
                "url": self._url(),
                "status": "SERVICE STARTED" if running else "SERVICE STOPPED",
            }

    def set_port(self, value: str) -> dict:
        with self.lock:
            if self._running():
                return self._result("Stop the service before changing the port.")
            cleaned = "".join(ch for ch in str(value) if ch.isdigit())[:5]
            self.port = cleaned or "8787"
            return self._result()

    def set_token(self, value: str) -> dict:
        with self.lock:
            if self._running():
                return self._result("Stop the service before changing the token.")
            self.token = str(value).strip() or secrets.token_urlsafe(12)
            return self._result()

    def regenerate_token(self) -> dict:
        with self.lock:
            if self._running():
                return self._result("Stop the service before regenerating the token.")
            self.token = secrets.token_urlsafe(12)
            return self._result("New token generated.")

    def start_service(self) -> dict:
        with self.lock:
            if self._running():
                return self._result("Service is already running.")
            try:
                port = int(self.port)
                if port <= 0 or port > 65535:
                    raise ValueError
            except ValueError:
                return self._result("Port must be between 1 and 65535.")

            thread = BridgeServerThread("0.0.0.0", port, self.token)
            self.server_thread = thread
            thread.start()

        thread.ready.wait(timeout=4)

        with self.lock:
            if thread.error:
                self.server_thread = None
                return self._result(f"Failed to start service: {thread.error}")
            return self._result("Service started.")

    def stop_service(self) -> dict:
        with self.lock:
            thread = self.server_thread
            self.server_thread = None
        if thread is not None:
            thread.stop()
            thread.join(timeout=2)
        return self._result("Service stopped.")

    def copy_url(self) -> dict:
        url = self.get_state()["url"]
        try:
            copy_text_to_clipboard(url)
            return self._result("URL copied to clipboard.")
        except Exception as exc:
            return self._result(f"Clipboard copy failed: {exc}")

    def open_url(self) -> dict:
        webbrowser.open(self.get_state()["url"])
        return self._result("Opened the voice input page.")

    def minimize_window(self) -> dict:
        if self.window is not None:
            self.window.minimize()
        return self._result()

    def toggle_maximize_window(self) -> dict:
        if self.window is not None:
            if self.maximized:
                self.window.restore()
                self.maximized = False
            else:
                self.window.maximize()
                self.maximized = True
        return self._result()

    def close_window(self) -> dict:
        state = self._result("Closing...")

        def destroy_later() -> None:
            self.shutdown()
            if self.window is not None:
                self.window.destroy()

        threading.Timer(0.05, destroy_later).start()
        return state

    def shutdown(self) -> None:
        self.stop_service()


def apply_window_chrome(window: webview.Window) -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.c_void_p(window.native.Handle.ToInt64())
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

        def colorref(hex_color: str) -> int:
            value = hex_color.lstrip("#")
            red = int(value[0:2], 16)
            green = int(value[2:4], 16)
            blue = int(value[4:6], 16)
            return red | (green << 8) | (blue << 16)

        def set_dwm_attribute(attribute: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))

        set_dwm_attribute(20, 1)
        set_dwm_attribute(19, 1)
        set_dwm_attribute(34, colorref("#1e3b2b"))
        set_dwm_attribute(35, colorref("#050807"))
        set_dwm_attribute(36, colorref("#dde7df"))
    except Exception:
        return


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This program injects text with Windows SendInput and must run on Windows.")

    api = DesktopApi()
    window = webview.create_window(
        "Flow Bridge",
        ui_url(),
        js_api=api,
        width=1240,
        height=860,
        min_size=(1120, 780),
        frameless=True,
        easy_drag=False,
        draggable=True,
        shadow=True,
        background_color="#050807",
    )
    api.window = window
    window.events.loaded += lambda: apply_window_chrome(window)
    window.events.closing += lambda: api.shutdown()
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
