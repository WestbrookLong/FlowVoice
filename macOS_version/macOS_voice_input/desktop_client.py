import asyncio
import base64
import io
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from queue import Queue
from typing import Any

from aiohttp import web
import qrcode
import webview

from server import create_app, get_lan_ip
from tunnel import CloudflaredTunnelThread, find_cloudflared


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def ui_index_path() -> Path:
    return app_root() / "desktop_ui" / "index.html"


def ui_url() -> str:
    dev_url = os.environ.get("MACOS_VOICE_INPUT_UI_DEV_URL")
    if dev_url:
        return dev_url

    index_path = ui_index_path()
    if not index_path.exists():
        raise SystemExit(f"Desktop UI is missing: {index_path}")
    return index_path.as_uri()


def copy_text_to_clipboard(text: str) -> None:
    process = subprocess.run(
        ["pbcopy"],
        input=text,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError("pbcopy failed")


def qr_data_url(value: str) -> str:
    image = qrcode.make(value or "about:blank").resize((420, 420))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


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
        self.tunnel_thread: CloudflaredTunnelThread | None = None
        self.events: Queue[Any] = Queue()
        self.public_base_url = ""
        self.tunnel_status = "PUBLIC OFFLINE"
        self.window: webview.Window | None = None
        self.maximized = False

    def _local_url(self) -> str:
        return f"http://{self.lan_ip}:{self.port}/?token={self.token}&v={self.page_version}"

    def _public_url(self) -> str:
        if not self.public_base_url:
            return ""
        return f"{self.public_base_url.rstrip('/')}/?token={self.token}&v={self.page_version}"

    def _running(self) -> bool:
        return self.server_thread is not None and self.server_thread.error is None

    def _tunnel_running(self) -> bool:
        return self.tunnel_thread is not None

    def _result(self, message: str = "") -> dict:
        return {"state": self.get_state(), "message": message}

    def _poll_tunnel_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except Exception:
                return

            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "tunnel_started":
                self.tunnel_status = "PUBLIC CONNECTING"
            elif event_type == "tunnel_url":
                self.public_base_url = str(event.get("url", ""))
                self.tunnel_status = "PUBLIC ONLINE"
            elif event_type == "tunnel_error":
                self.public_base_url = ""
                self.tunnel_status = f"PUBLIC ERROR: {event.get('message', 'unknown error')}"
                self.tunnel_thread = None
            elif event_type == "tunnel_exit":
                self.public_base_url = ""
                self.tunnel_status = f"PUBLIC STOPPED ({event.get('code')})"
                self.tunnel_thread = None

    def get_state(self) -> dict:
        with self.lock:
            self._poll_tunnel_events()
            running = self._running()
            local_url = self._local_url()
            public_url = self._public_url()
            active_url = public_url or local_url
            return {
                "running": running,
                "tunnelRunning": self._tunnel_running(),
                "token": self.token,
                "ip": self.lan_ip,
                "port": self.port,
                "url": local_url,
                "publicUrl": public_url,
                "activeUrl": active_url,
                "qrDataUrl": qr_data_url(active_url),
                "status": "SERVICE STARTED" if running else "SERVICE STOPPED",
                "tunnelStatus": self.tunnel_status,
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
        thread: BridgeServerThread | None = None
        with self.lock:
            if self._running():
                if self._tunnel_running():
                    return self._result("Service and public tunnel are already running.")
            else:
                cloudflared_path = find_cloudflared()
                if cloudflared_path is None:
                    return self._result("cloudflared was not found. Install it with: brew install cloudflared")
                try:
                    port = int(self.port)
                    if port <= 0 or port > 65535:
                        raise ValueError
                except ValueError:
                    return self._result("Port must be between 1 and 65535.")

                thread = BridgeServerThread("0.0.0.0", port, self.token)
                self.server_thread = thread
                thread.start()

        if thread is not None:
            thread.ready.wait(timeout=4)

            with self.lock:
                if thread.error:
                    self.server_thread = None
                    return self._result(f"Failed to start service: {thread.error}")

        tunnel_result = self.start_tunnel()
        tunnel_message = tunnel_result.get("message", "")
        if "not found" in tunnel_message or "failed" in tunnel_message.lower():
            return self._result(f"Service started, but public tunnel failed: {tunnel_message}")
        return self._result("Service and public tunnel starting.")

    def stop_service(self) -> dict:
        self.stop_tunnel()
        with self.lock:
            thread = self.server_thread
            self.server_thread = None
        if thread is not None:
            thread.stop()
            thread.join(timeout=2)
        return self._result("Service stopped.")

    def start_tunnel(self) -> dict:
        with self.lock:
            if self._tunnel_running():
                return self._result("Public tunnel is already running.")
            try:
                port = int(self.port)
                if port <= 0 or port > 65535:
                    raise ValueError
            except ValueError:
                return self._result("Port must be between 1 and 65535.")

        cloudflared_path = find_cloudflared()
        if cloudflared_path is None:
            return self._result("cloudflared was not found. Install it with: brew install cloudflared")

        if not self._running():
            start_result = self.start_service()
            if not start_result["state"]["running"]:
                return start_result

        with self.lock:
            self.public_base_url = ""
            self.tunnel_status = "PUBLIC STARTING"
            self.tunnel_thread = CloudflaredTunnelThread(cloudflared_path, int(self.port), self.events)
            self.tunnel_thread.start()
            return self._result("Public tunnel starting.")

    def stop_tunnel(self) -> dict:
        with self.lock:
            thread = self.tunnel_thread
            self.tunnel_thread = None
            self.public_base_url = ""
            self.tunnel_status = "PUBLIC OFFLINE"
        if thread is not None:
            thread.stop()
        return self._result("Public tunnel stopped.")

    def copy_url(self) -> dict:
        url = self.get_state()["activeUrl"]
        try:
            copy_text_to_clipboard(url)
            return self._result("URL copied to clipboard.")
        except Exception as exc:
            return self._result(f"Clipboard copy failed: {exc}")

    def copy_public_url(self) -> dict:
        url = self.get_state()["publicUrl"]
        if not url:
            return self._result("Public URL is not ready.")
        try:
            copy_text_to_clipboard(url)
            return self._result("Public URL copied to clipboard.")
        except Exception as exc:
            return self._result(f"Clipboard copy failed: {exc}")

    def open_url(self) -> dict:
        webbrowser.open(self.get_state()["activeUrl"])
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


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This desktop client is for macOS.")

    api = DesktopApi()
    window = webview.create_window(
        "Flow Voice",
        ui_url(),
        js_api=api,
        width=1180,
        height=820,
        min_size=(1040, 720),
        frameless=True,
        easy_drag=False,
        draggable=True,
        shadow=True,
        background_color="#050807",
    )
    api.window = window
    window.events.closing += lambda: api.shutdown()
    webview.start(debug=False)


if __name__ == "__main__":
    main()
