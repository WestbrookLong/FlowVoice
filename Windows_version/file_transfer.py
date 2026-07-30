import ctypes
import io
import json
import mimetypes
import os
import re
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

from aiohttp import web


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_FILES = 20
SETTINGS_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FlowBridge" / "file_transfer.json"
DEFAULT_UPLOAD_DIR = Path.home() / "Downloads" / "FlowVoice Uploads"
VALID_IMAGE_CLIPBOARD_MODES = {"image", "path"}
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _safe_filename(value: str, fallback: str = "shared-file") -> str:
    name = Path(value or fallback).name.strip().rstrip(". ")
    name = INVALID_FILENAME_CHARS.sub("_", name)
    return name[:180] or fallback


def _unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 10_000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{int(time.time() * 1000)}{suffix}"


def copy_image_to_windows_clipboard(path: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Image clipboard is only supported on Windows.")
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required to copy images to the clipboard.") from exc

    with Image.open(path) as image:
        converted = image.convert("RGB")
        buffer = io.BytesIO()
        converted.save(buffer, "BMP")
        dib = buffer.getvalue()[14:]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    GMEM_MOVEABLE = 0x0002
    CF_DIB = 8
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    user32.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p)
    user32.SetClipboardData.restype = ctypes.c_void_p

    last_error: Exception | None = None
    for _ in range(6):
        if user32.OpenClipboard(None):
            break
        last_error = ctypes.WinError(ctypes.get_last_error())
        time.sleep(0.05)
    else:
        raise last_error or RuntimeError("Unable to open clipboard.")

    handle = None
    try:
        if not user32.EmptyClipboard():
            raise ctypes.WinError(ctypes.get_last_error())
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        locked = kernel32.GlobalLock(handle)
        if not locked:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(locked, dib, len(dib))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_DIB, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        handle = None
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


class FileTransferManager:
    def __init__(
        self,
        *,
        copy_text: Callable[[str], None],
        log: Callable[[str], None],
        settings_path: Path = SETTINGS_PATH,
    ) -> None:
        self.copy_text = copy_text
        self.log = log
        self.settings_path = settings_path
        self.lock = threading.RLock()
        saved = _load_json(settings_path)
        self.save_directory = Path(saved.get("saveDirectory") or DEFAULT_UPLOAD_DIR).expanduser()
        mode = str(saved.get("imageClipboardMode") or "image")
        self.image_clipboard_mode = mode if mode in VALID_IMAGE_CLIPBOARD_MODES else "image"
        self.auto_screenshot_upload = bool(saved.get("autoScreenshotUpload", False))
        self.mobile_monitor_status = "disconnected"
        self.last_upload: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "saveDirectory": str(self.save_directory),
                "imageClipboardMode": self.image_clipboard_mode,
                "autoScreenshotUpload": self.auto_screenshot_upload,
                "mobileMonitorStatus": self.mobile_monitor_status,
                "maxUploadMb": MAX_UPLOAD_BYTES // (1024 * 1024),
                "lastUpload": dict(self.last_upload) if self.last_upload else None,
            }

    def mobile_config(self) -> dict[str, Any]:
        with self.lock:
            return {
                "type": "file_upload_config",
                "autoScreenshotUpload": self.auto_screenshot_upload,
            }

    def update_mobile_status(self, status: str) -> None:
        allowed = {"disabled", "listening", "permission_required", "error", "disconnected"}
        with self.lock:
            self.mobile_monitor_status = status if status in allowed else "error"

    def update_settings(self, payload: dict[str, Any]) -> None:
        with self.lock:
            directory = payload.get("saveDirectory")
            if directory is not None:
                candidate = Path(str(directory).strip()).expanduser()
                if not str(candidate):
                    raise ValueError("Save directory cannot be empty.")
                candidate.mkdir(parents=True, exist_ok=True)
                self.save_directory = candidate.resolve()
            mode = payload.get("imageClipboardMode")
            if mode is not None:
                mode = str(mode)
                if mode not in VALID_IMAGE_CLIPBOARD_MODES:
                    raise ValueError("Invalid image clipboard mode.")
                self.image_clipboard_mode = mode
            auto_upload = payload.get("autoScreenshotUpload")
            if auto_upload is not None:
                self.auto_screenshot_upload = bool(auto_upload)
            _atomic_write_json(
                self.settings_path,
                {
                    "saveDirectory": str(self.save_directory),
                    "imageClipboardMode": self.image_clipboard_mode,
                    "autoScreenshotUpload": self.auto_screenshot_upload,
                },
            )

    def _copy_paths(self, saved: list[dict[str, Any]]) -> None:
        value = "\n".join(item["path"] for item in saved)
        last_error: Exception | None = None
        for _ in range(6):
            try:
                self.copy_text(value)
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.05)
        raise last_error or RuntimeError("Unable to write paths to the clipboard.")

    async def handle_upload(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        saved: list[dict[str, Any]] = []
        total_bytes = 0
        self.save_directory.mkdir(parents=True, exist_ok=True)

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != "files" or not part.filename:
                continue
            if len(saved) >= MAX_UPLOAD_FILES:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=MAX_UPLOAD_FILES,
                    actual_size=len(saved) + 1,
                    text=f"At most {MAX_UPLOAD_FILES} files are allowed.",
                )

            filename = _safe_filename(part.filename)
            target = _unique_path(self.save_directory, filename)
            temporary = target.with_suffix(target.suffix + ".part")
            file_bytes = 0
            try:
                with temporary.open("wb") as output:
                    while True:
                        chunk = await part.read_chunk(size=256 * 1024)
                        if not chunk:
                            break
                        file_bytes += len(chunk)
                        total_bytes += len(chunk)
                        if total_bytes > MAX_UPLOAD_BYTES:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=MAX_UPLOAD_BYTES,
                                actual_size=total_bytes,
                            )
                        output.write(chunk)
                temporary.replace(target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise

            content_type = part.headers.get("Content-Type") or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            saved.append(
                {
                    "name": target.name,
                    "path": str(target.resolve()),
                    "mimeType": content_type,
                    "size": file_bytes,
                }
            )

        if not saved:
            raise web.HTTPBadRequest(text="No files were provided.")

        clipboard_mode = "path"
        try:
            only_file = saved[0] if len(saved) == 1 else None
            is_image = only_file is not None and (
                str(only_file["mimeType"]).startswith("image/")
                or Path(only_file["path"]).suffix.lower()
                in {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
            )
            if (
                only_file is not None
                and is_image
                and self.image_clipboard_mode == "image"
            ):
                copy_image_to_windows_clipboard(Path(only_file["path"]))
                clipboard_mode = "image"
            else:
                self._copy_paths(saved)
        except Exception as exc:
            self.log(f"[file-upload] clipboard failed: {exc}")
            clipboard_mode = "failed"

        with self.lock:
            self.last_upload = {
                "files": saved,
                "clipboardMode": clipboard_mode,
                "receivedAt": int(time.time()),
            }
        self.log(
            f"[file-upload] saved {len(saved)} file(s), {total_bytes} bytes; clipboard={clipboard_mode}"
        )
        return web.json_response(
            {
                "ok": True,
                "files": saved,
                "clipboardMode": clipboard_mode,
            }
        )
