import re
import shutil
import subprocess
import threading
from pathlib import Path
from queue import Queue
from typing import Any


CLOUDFLARED_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
DEFAULT_CLOUDFLARED_PATHS = (
    Path("/opt/homebrew/bin/cloudflared"),
    Path("/usr/local/bin/cloudflared"),
)


def find_cloudflared() -> Path | None:
    for path in DEFAULT_CLOUDFLARED_PATHS:
        if path.exists():
            return path
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    return None


class CloudflaredTunnelThread(threading.Thread):
    def __init__(self, cloudflared_path: Path, port: int, events: Queue[Any]) -> None:
        super().__init__(daemon=True)
        self.cloudflared_path = cloudflared_path
        self.port = port
        self.events = events
        self.process: subprocess.Popen[str] | None = None
        self._stopping = threading.Event()

    def run(self) -> None:
        command = [
            str(self.cloudflared_path),
            "tunnel",
            "--url",
            f"http://127.0.0.1:{self.port}",
        ]

        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            self.events.put({"type": "tunnel_error", "message": str(exc)})
            return

        self.events.put({"type": "tunnel_started"})
        seen_url = False

        try:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                match = CLOUDFLARED_URL_PATTERN.search(line)
                if match and not seen_url:
                    seen_url = True
                    self.events.put({"type": "tunnel_url", "url": match.group(0)})

            return_code = self.process.wait()
            if not self._stopping.is_set():
                self.events.put({"type": "tunnel_exit", "code": return_code})
        finally:
            self.process = None

    def stop(self) -> None:
        self._stopping.set()
        if self.process is None:
            return
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
