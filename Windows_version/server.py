import argparse
import ctypes
import difflib
import json
import logging
import os
import re
import secrets
import socket
import sys
import unicodedata
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

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
INPUT_KEYBOARD = 1
VK_BACK = 0x08
VK_RETURN = 0x0D
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
USER32 = ctypes.WinDLL("user32", use_last_error=True)

MAX_SYNC_TEXT_LEN = 5000
MAX_RAW_TEXT_LEN = 50000


class PhoneControlHub:
    def __init__(self) -> None:
        self.connections: set[web.WebSocketResponse] = set()

    def attach(self, ws: web.WebSocketResponse) -> None:
        self.connections.add(ws)

    def detach(self, ws: web.WebSocketResponse) -> None:
        self.connections.discard(ws)

    def has_connections(self) -> bool:
        return any(not ws.closed for ws in self.connections)

    async def broadcast(self, payload: dict[str, Any]) -> int:
        sent = 0
        stale: list[web.WebSocketResponse] = []
        for ws in tuple(self.connections):
            if ws.closed:
                stale.append(ws)
                continue
            try:
                await ws.send_json(payload)
                sent += 1
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.connections.discard(ws)
        return sent
MAX_DELETE_ALL_BACKSPACES = 5000
BOUNDARY_PUNCTUATION = ",.;:!?，。！？、；："
BOUNDARY_PUNCTUATION_SET = set(BOUNDARY_PUNCTUATION)
LINE_BREAK_PATTERN = re.compile(r"\r?\n")
VOICE_COMMAND_PATTERN = re.compile(
    r"((?:delete\s+all)|(?:back\s*space)|back|enter)\s*$",
    re.IGNORECASE,
)
BOUNDARY_PUNCTUATION_PATTERN = re.compile(r"[,.!?;:，。！？、；：]")
SPOKEN_PUNCTUATION_PATTERN = re.compile(
    "左双引号|右双引号|左单引号|右单引号|左括号|右括号|省略号|破折号|感叹号|叹号|逗号|句号|顿号|问号|冒号|分号|引号",
)


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
    def __init__(self, on_text_inserted: Callable[[str], None] | None = None) -> None:
        self.text = ""
        self.on_text_inserted = on_text_inserted

    def replace(self, new_text: str) -> None:
        prefix_len = common_prefix_len(self.text, new_text)
        delete_count = len(list(self.text[prefix_len:]))
        insert_text = new_text[prefix_len:]
        if delete_count:
            backspace(delete_count)
        if insert_text:
            type_text(insert_text)
            if self.on_text_inserted is not None:
                self.on_text_inserted(insert_text)
        self.text = new_text

    def reset(self) -> None:
        self.text = ""


class BridgeSettings:
    def __init__(
        self,
        filter_punctuation: bool = False,
        convert_spoken_punctuation: bool = False,
        enable_voice_commands: bool = False,
    ) -> None:
        self.filter_punctuation = filter_punctuation
        self.convert_spoken_punctuation = filter_punctuation and convert_spoken_punctuation
        self.enable_voice_commands = enable_voice_commands

    @classmethod
    def from_payload(cls, payload: Any) -> "BridgeSettings":
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ValueError("settings must be an object")
        filter_punctuation = payload.get("filterPunctuation", False)
        convert_spoken_punctuation = payload.get("convertSpokenPunctuation", False)
        enable_voice_commands = payload.get("enableVoiceCommands", False)
        for key, value in (
            ("filterPunctuation", filter_punctuation),
            ("convertSpokenPunctuation", convert_spoken_punctuation),
            ("enableVoiceCommands", enable_voice_commands),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"settings.{key} must be a boolean")
        return cls(filter_punctuation, convert_spoken_punctuation, enable_voice_commands)


class FlowInputSession:
    def __init__(self, on_text_inserted: Callable[[str], None] | None = None) -> None:
        self.on_text_inserted = on_text_inserted
        self.text_session = TextSession(on_text_inserted)
        self.raw_text = ""
        self.raw_session_start = 0
        self.last_seq = 0

    def should_process(self, seq: Any) -> bool:
        if not isinstance(seq, int):
            return True
        if seq <= self.last_seq:
            log(f"[ws] skip stale seq={seq}, last_seq={self.last_seq}")
            return False
        self.last_seq = seq
        return True

    def sync_processed_text(self, text: str) -> None:
        normalized = text[:MAX_SYNC_TEXT_LEN]
        preview = normalized[-120:]
        log(
            f"[sync] replace {len(self.text_session.text)} chars -> {len(normalized)} chars; tail={preview!r}",
        )
        self.text_session.replace(normalized)

    def reset(self) -> None:
        log("[sync] reset session")
        self.raw_text = ""
        self.raw_session_start = 0
        self.text_session.reset()

    def rebase(self, raw_text: str) -> None:
        self.raw_text = trim_raw_text(raw_text, self)
        self.raw_session_start = len(self.raw_text)
        self.text_session.reset()
        log(f"[sync] rebase at {self.raw_session_start} raw chars")

    def sync_state(self, raw_text: str, settings: BridgeSettings) -> None:
        next_raw_text = raw_text[-MAX_RAW_TEXT_LEN:]
        self.raw_session_start = map_text_offset(
            self.raw_text,
            next_raw_text,
            self.raw_session_start,
        )
        self.raw_text = next_raw_text

        iterations = 0
        while iterations < 64:
            iterations += 1
            active_text = self.raw_text[self.raw_session_start :]
            if not active_text:
                self.sync_processed_text("")
                return

            line_break_match = LINE_BREAK_PATTERN.search(active_text)
            if line_break_match is not None:
                prefix_raw = active_text[: line_break_match.start()]
                self.sync_processed_text(render_text(prefix_raw, settings))
                log("[inject] enter (line break)")
                press_key(VK_RETURN)
                self.raw_session_start += line_break_match.end()
                self.text_session.reset()
                continue

            command = parse_voice_command(active_text) if settings.enable_voice_commands else None
            if command is not None:
                command_name, prefix_raw, consumed_length = command
                previous_synced_text = self.text_session.text
                prefix_text = render_text(prefix_raw, settings)
                self.sync_processed_text(prefix_text)
                if command_name == "enter":
                    log("[inject] enter (voice command)")
                    press_key(VK_RETURN)
                elif command_name == "back":
                    log("[inject] backspace 1 (voice command)")
                    send_backspace_chunks(1)
                elif command_name == "backspace":
                    target_text = prefix_text or previous_synced_text
                    count = backspace_count_to_previous_punctuation(target_text)
                    log(f"[inject] backspace {count} (voice command)")
                    send_backspace_chunks(count)
                elif command_name == "delete all":
                    count = max(MAX_DELETE_ALL_BACKSPACES, len(previous_synced_text), len(prefix_text))
                    log(f"[inject] backspace {count} (delete all)")
                    send_backspace_chunks(count)
                self.raw_session_start += consumed_length
                self.text_session.reset()
                continue

            self.sync_processed_text(render_text(active_text, settings))
            return

        raise ValueError("too many pending control events in raw text")


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


def map_text_offset(previous: str, current: str, offset: int) -> int:
    offset = max(0, min(offset, len(previous)))
    if offset == 0 or previous == current:
        return min(offset, len(current))
    if current.startswith(previous):
        return offset
    if previous.startswith(current):
        return min(offset, len(current))

    prefix_len = common_prefix_len(previous, current)
    if prefix_len >= offset:
        return offset

    matcher = difflib.SequenceMatcher(None, previous, current, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if offset <= old_start:
            return new_start
        if tag == "equal" and offset <= old_end:
            return new_start + (offset - old_start)
        if tag != "insert" and offset <= old_end:
            return new_end
    return len(current)


def trim_raw_text(raw_text: str, session: FlowInputSession) -> str:
    if len(raw_text) <= MAX_RAW_TEXT_LEN:
        return raw_text
    overflow = len(raw_text) - MAX_RAW_TEXT_LEN
    session.raw_session_start = max(0, session.raw_session_start - overflow)
    return raw_text[overflow:]


def send_backspace_chunks(count: int) -> None:
    remaining = max(0, count)
    while remaining > 0:
        chunk = min(remaining, 1000)
        backspace(chunk)
        remaining -= chunk


def is_cjk(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    )


def parse_voice_command(raw_text: str) -> tuple[str, str, int] | None:
    match = VOICE_COMMAND_PATTERN.search(raw_text)
    if match is None:
        return None
    command_start = match.start(1)
    prefix_end = command_start
    if command_start > 0:
        boundary_char = raw_text[command_start - 1]
        if boundary_char.isspace() or boundary_char in BOUNDARY_PUNCTUATION_SET:
            prefix_end -= 1
        elif not is_cjk(boundary_char):
            return None
    raw_command = re.sub(r"\s+", " ", match.group(1).lower()).strip()
    normalized_command = "backspace" if raw_command == "back space" else raw_command
    prefix_raw = raw_text[:prefix_end].rstrip()
    return normalized_command, prefix_raw, match.end()


def strip_punctuation(text: str) -> str:
    return "".join(
        char
        for char in text
        if unicodedata.category(char)[:1] not in {"P", "S"}
    )


def nearby_non_space(text: str, index: int, direction: int) -> str:
    while 0 <= index < len(text):
        if not text[index].isspace():
            return text[index]
        index += direction
    return ""


def is_latin_or_digit(char: str) -> bool:
    return bool(char) and bool(re.match(r"[A-Za-z0-9]", char))


def punctuation_style(text: str, start: int, end: int) -> str:
    prev_char = nearby_non_space(text, start - 1, -1)
    next_char = nearby_non_space(text, end, 1)
    if is_cjk(prev_char) or is_cjk(next_char):
        return "zh"
    if is_latin_or_digit(prev_char) or is_latin_or_digit(next_char):
        return "en"
    return "zh"


def spoken_punctuation_symbol(phrase: str, text: str, start: int, end: int) -> str:
    is_english = punctuation_style(text, start, end) == "en"
    mapping = {
        "逗号": "," if is_english else "，",
        "句号": "." if is_english else "。",
        "顿号": "、",
        "问号": "?" if is_english else "？",
        "感叹号": "!" if is_english else "！",
        "叹号": "!" if is_english else "！",
        "冒号": ":" if is_english else "：",
        "分号": ";" if is_english else "；",
        "省略号": "..." if is_english else "……",
        "破折号": "--" if is_english else "——",
        "左括号": "(" if is_english else "（",
        "右括号": ")" if is_english else "）",
        "左双引号": '"' if is_english else "“",
        "右双引号": '"' if is_english else "”",
        "左单引号": "'" if is_english else "‘",
        "右单引号": "'" if is_english else "’",
        "引号": '"' if is_english else "”",
    }
    return mapping.get(phrase, phrase)


def cleanup_converted_punctuation(text: str) -> str:
    text = re.sub(r"\s+([，。！？、；：）】》」』”’])", r"\1", text)
    text = re.sub(r"([（【《「『“‘])\s+", r"\1", text)
    text = re.sub(r"\s+([（【《「『“‘])", r"\1", text)
    text = re.sub(r"([，。！？、；：])\s+", r"\1", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return re.sub(r"([,.!?;:])(?=[A-Za-z0-9])", r"\1 ", text)


def convert_spoken_punctuation(text: str) -> str:
    def replace_phrase(match: re.Match[str]) -> str:
        return spoken_punctuation_symbol(match.group(0), text, match.start(), match.end())

    return cleanup_converted_punctuation(SPOKEN_PUNCTUATION_PATTERN.sub(replace_phrase, text))


def render_text(raw_text: str, settings: BridgeSettings) -> str:
    text = raw_text
    if settings.filter_punctuation:
        text = strip_punctuation(text)
        if settings.convert_spoken_punctuation:
            text = convert_spoken_punctuation(text)
    return text[:MAX_SYNC_TEXT_LEN]


def backspace_count_to_previous_punctuation(text: str) -> int:
    chars = list(text)
    search_end = len(chars)
    while search_end > 0 and chars[search_end - 1].isspace():
        search_end -= 1
    while search_end > 0 and BOUNDARY_PUNCTUATION_PATTERN.match(chars[search_end - 1]):
        search_end -= 1
    for index in range(search_end - 1, -1, -1):
        if BOUNDARY_PUNCTUATION_PATTERN.match(chars[index]):
            return len(chars) - index - 1
    return max(len(chars), 1000)


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
                normalized.append({"type": "insert", "text": text[:MAX_SYNC_TEXT_LEN]})
        elif op_type == "enter":
            normalized.append({"type": "enter"})
        elif op_type == "backspace":
            count = op.get("count")
            if not isinstance(count, int):
                raise ValueError("backspace.count must be an integer")
            if count > 0:
                normalized.append({"type": "backspace", "count": min(count, MAX_DELETE_ALL_BACKSPACES)})
        else:
            raise ValueError(f"unsupported op type: {op_type}")
    return normalized


def create_app(
    token: str,
    text_agent: Any = None,
    typing_stats: Any = None,
    input_gate: Any = None,
    voice_ask: Any = None,
    phone_control: PhoneControlHub | None = None,
    voice_hold_state_callback: Callable[[bool, str], None] | None = None,
    mobile_input_blocked_callback: Callable[[], bool] | None = None,
    file_transfer: Any = None,
) -> web.Application:
    app = web.Application(client_max_size=101 * 1024 * 1024)
    session = FlowInputSession(
        (lambda text: typing_stats.record(text, "mobile")) if typing_stats is not None else None
    )
    text_agent_route_active = False

    def require_token(request: web.Request, payload: dict[str, Any] | None = None) -> None:
        request_token = request.query.get("token")
        authorization = request.headers.get("Authorization", "")
        bearer_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        payload_token = payload.get("token") if isinstance(payload, dict) else None
        if request_token != token and payload_token != token and bearer_token != token:
            raise web.HTTPUnauthorized(text="invalid token")

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

    async def text_agent_state(request: web.Request) -> web.Response:
        require_token(request)
        if text_agent is None:
            raise web.HTTPServiceUnavailable(text="text agent is not configured")
        return web.json_response(text_agent.get_state())

    async def text_agent_mode(request: web.Request) -> web.Response:
        payload = await request.json()
        require_token(request, payload)
        if text_agent is None:
            raise web.HTTPServiceUnavailable(text="text agent is not configured")
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="payload must be an object")
        text_agent.set_mode(bool(payload.get("enabled", False)))
        return web.json_response(text_agent.get_state())

    async def file_upload(request: web.Request) -> web.Response:
        log(f"[file-upload] request method={request.method} peer={request.remote or 'unknown'}")
        require_token(request)
        if file_transfer is None:
            raise web.HTTPServiceUnavailable(text="file transfer is not configured")
        return await file_transfer.handle_upload(request)

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        nonlocal text_agent_route_active
        if request.query.get("token") != token:
            raise web.HTTPUnauthorized(text="invalid token")

        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        peer = request.remote or "unknown"
        log(f"[ws] connected: {peer}")
        if phone_control is not None:
            phone_control.attach(ws)
        await ws.send_json({"type": "ready"})
        input_gate_blocked = False
        paused_raw_text: str | None = None

        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
                if payload.get("token") != token:
                    raise ValueError("invalid token")
                if not session.should_process(payload.get("seq")):
                    await ws.send_json({"type": "ack", "seq": payload.get("seq")})
                    continue

                message_type = payload.get("type")
                if message_type == "voice_hold_state":
                    active = payload.get("active")
                    reason = payload.get("reason", "released")
                    if not isinstance(active, bool):
                        raise ValueError("voice_hold_state.active must be a boolean")
                    if reason not in {
                        "started",
                        "released",
                        "gesture_cancelled",
                        "pending_cancelled",
                        "overlay_closed",
                        "service_stopped",
                        "keyboard_hidden",
                    }:
                        raise ValueError("voice_hold_state.reason is invalid")
                    if voice_hold_state_callback is not None:
                        voice_hold_state_callback(active, reason)
                    await ws.send_json({"type": "ack", "seq": payload.get("seq")})
                    continue
                voice_ask_mobile_route = False
                if message_type in {"sync_state", "rebase_settings"} and voice_ask is not None:
                    text = payload.get("text")
                    if not isinstance(text, str):
                        raise ValueError(f"{message_type}.text must be a string")
                    BridgeSettings.from_payload(payload.get("settings"))
                    voice_ask.observe_source("mobile", text)
                    voice_ask_mobile_route = voice_ask.should_capture("mobile")
                if (
                    not voice_ask_mobile_route
                    and mobile_input_blocked_callback is not None
                    and mobile_input_blocked_callback()
                ):
                    if not input_gate_blocked:
                        if text_agent_route_active:
                            text_agent_route_active = False
                        session.reset()
                    input_gate_blocked = True
                    paused_raw_text = payload.get("text") if isinstance(payload.get("text"), str) else paused_raw_text
                    await ws.send_json({"type": "ack", "seq": payload.get("seq")})
                    continue
                if not voice_ask_mobile_route and input_gate is not None and input_gate.is_paused():
                    if not input_gate_blocked:
                        if text_agent_route_active:
                            text_agent_route_active = False
                        session.reset()
                    if message_type == "sync_state":
                        text = payload.get("text")
                        if isinstance(text, str):
                            paused_raw_text = text
                    input_gate_blocked = True
                    await ws.send_json({"type": "ack", "seq": payload.get("seq")})
                    continue

                resumed_from_input_gate = input_gate_blocked
                if resumed_from_input_gate:
                    input_gate_blocked = False

                if message_type == "rebase_settings":
                    text = payload.get("text")
                    if not isinstance(text, str):
                        raise ValueError("rebase_settings.text must be a string")
                    BridgeSettings.from_payload(payload.get("settings"))
                    if text_agent is not None:
                        text_agent.observe_mobile_text(text)
                    session.rebase(text)
                elif message_type == "sync_state":
                    text = payload.get("text")
                    if not isinstance(text, str):
                        raise ValueError("sync_state.text must be a string")
                    settings = BridgeSettings.from_payload(payload.get("settings"))
                    if resumed_from_input_gate:
                        if text_agent is not None:
                            text_agent.reset_capture_baseline(text)
                        session.reset()
                        paused_baseline = paused_raw_text if paused_raw_text is not None else text
                        if text.startswith(paused_baseline):
                            session.raw_session_start = len(paused_baseline)
                        else:
                            session.raw_session_start = len(text)
                        session.raw_text = trim_raw_text(text, session)
                        paused_raw_text = None
                        session.sync_state(text, settings)
                    elif (
                        not voice_ask_mobile_route
                        and text_agent is not None
                        and text_agent.should_capture_text()
                    ):
                        text_agent_route_active = True
                        active_source_text = text_agent.capture_active_source(text)
                        text_agent.update_text(
                            text,
                            render_text(active_source_text, settings),
                            active_source_text=active_source_text,
                        )
                    else:
                        if text_agent_route_active:
                            baseline_text = text_agent.get_last_mobile_text() if text_agent is not None else text
                            session.reset()
                            session.raw_text = trim_raw_text(baseline_text, session)
                            session.raw_session_start = len(session.raw_text)
                            text_agent_route_active = False
                        if text_agent is not None:
                            text_agent.observe_mobile_text(text)
                        session.sync_state(text, settings)
                elif message_type == "sync_text":
                    text = payload.get("text")
                    if not isinstance(text, str):
                        raise ValueError("sync_text.text must be a string")
                    if resumed_from_input_gate:
                        session.text_session.text = text[:MAX_SYNC_TEXT_LEN]
                    else:
                        session.sync_processed_text(text)
                elif message_type == "insert_text":
                    text = payload.get("text")
                    if not isinstance(text, str):
                        raise ValueError("insert_text.text must be a string")
                    if text not in {"，", "。", ",", "."}:
                        raise ValueError("insert_text.text must be a supported punctuation mark")
                    log(f"[inject] punctuation button {text!r}")
                    type_text(text)
                    if typing_stats is not None:
                        typing_stats.record(text, "mobile")
                elif message_type == "reset_session":
                    if text_agent is not None and text_agent.should_capture_text():
                        text_agent.update_text("", "", active_source_text="")
                    else:
                        session.reset()
                else:
                    ops = validate_ops(payload)
                    for op in ops:
                        if op["type"] == "insert":
                            if input_gate is not None and input_gate.is_paused():
                                continue
                            log(f"[inject] insert {len(op['text'])} chars: {op['text']!r}")
                            type_text(op["text"])
                            if typing_stats is not None:
                                typing_stats.record(op["text"], "mobile")
                        elif op["type"] == "enter":
                            if input_gate is not None and input_gate.is_paused():
                                continue
                            log("[inject] enter")
                            press_key(VK_RETURN)
                        elif op["type"] == "backspace":
                            if input_gate is not None and input_gate.is_paused():
                                continue
                            log(f"[inject] backspace {op['count']}")
                            send_backspace_chunks(op["count"])
                await ws.send_json({"type": "ack", "seq": payload.get("seq")})
            except Exception as exc:
                log(f"[error] {exc}")
                await ws.send_json({"type": "error", "message": str(exc)})

        if phone_control is not None:
            phone_control.detach(ws)
            if not phone_control.has_connections() and voice_hold_state_callback is not None:
                voice_hold_state_callback(False, "service_stopped")
        log(f"[ws] disconnected: {peer}")
        return ws

    app.router.add_get("/", index)
    app.router.add_post("/", file_upload)
    app.router.add_get("/health", health)
    app.router.add_get("/api/text-agent/state", text_agent_state)
    app.router.add_post("/api/text-agent/mode", text_agent_mode)
    app.router.add_post("/api/files/upload", file_upload)
    app.router.add_post("/api/files/upload/", file_upload)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static", STATIC_DIR)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phone realtime input bridge for Windows.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host, default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8787, help="Bind port, default: 8787")
    parser.add_argument("--token", default=None, help="Session token. Defaults to a random token.")
    parser.add_argument(
        "--test-text",
        default=None,
        help="Type text into the current Windows focus after a countdown, then exit.",
    )
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
