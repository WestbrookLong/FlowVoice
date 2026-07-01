import asyncio
import ctypes
from ctypes import wintypes
import os
import queue
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from aiohttp import web
import webview

from asr.base import ASREvent, StreamingASREngine
from asr.baidu_engine import DEFAULT_BAIDU_DEV_PID, BaiduSpeechEngine
from asr.bert_reranker import DEFAULT_BERT_RERANKER_MODEL
from asr.endpointing import EndpointConfig, EndpointDecision, EndpointDetector
from asr.funasr_candidate_streaming_engine import FunASRCandidateStreamingEngine
from asr.funasr_offline_engine import FunASROfflineEngine
from asr.funasr_streaming_engine import DEFAULT_STREAMING_MODEL, FunASRStreamingEngine
from asr.punctuation import PunctuationEngine
from asr.vosk_engine import VoskEngine
from input_gate import InputGate
from server import BridgeSettings, FlowInputSession, create_app, get_lan_ip, log, render_text, send_backspace_chunks, type_text
from text_agent import TextAgentManager
from typing_stats import TypingStats


DESKTOP_VOICE_MODEL_NAME = "vosk-model-small-cn-0.22"
DESKTOP_VOICE_DEFAULT_CONFIG = {
    "engine": "vosk",
    "funasrMode": "offline",
    "funasrModel": "iic/SenseVoiceSmall",
    "funasrStreamingChunkMs": 600,
    "baiduDevPid": DEFAULT_BAIDU_DEV_PID,
    "semanticReranker": "bert",
    "semanticModel": DEFAULT_BERT_RERANKER_MODEL,
    "punctuationStrategy": "spoken",
    "voiceCommands": True,
    "hotwords": "",
}
VALID_DESKTOP_VOICE_ENGINES = {"vosk", "funasr", "baidu"}
VALID_FUNASR_MODELS = {"iic/SenseVoiceSmall", "paraformer-zh"}
VALID_FUNASR_MODES = {"offline", "streaming", "candidate_streaming"}
VALID_SEMANTIC_RERANKERS = {"bert", "heuristic"}
VALID_PUNCTUATION_STRATEGIES = {"spoken", "model", "none"}
DESKTOP_VOICE_VIRTUAL_RESET_CHARS = 50


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


def desktop_voice_model_path() -> Path:
    configured = os.environ.get("FLOWVOICE_VOSK_MODEL")
    if configured:
        return Path(configured).expanduser().resolve()
    return app_root() / "models" / DESKTOP_VOICE_MODEL_NAME


def should_insert_space(left: str, right: str) -> bool:
    return bool(left and right and left[-1].isascii() and right[0].isascii() and left[-1].isalnum() and right[0].isalnum())


def append_recognized_text(base: str, addition: str) -> str:
    if not addition:
        return base
    if should_insert_space(base, addition):
        return f"{base} {addition}"
    return f"{base}{addition}"


def normalize_desktop_voice_config(value: dict | None) -> dict:
    source = value if isinstance(value, dict) else {}
    config = dict(DESKTOP_VOICE_DEFAULT_CONFIG)
    env_engine = os.environ.get("FLOWVOICE_DESKTOP_ENGINE")
    if env_engine:
        source = {**source, "engine": env_engine}
    env_baidu_dev_pid = os.environ.get("FLOWVOICE_BAIDU_DEV_PID")
    if env_baidu_dev_pid:
        source = {**source, "baiduDevPid": env_baidu_dev_pid}
    engine = str(source.get("engine", config["engine"])).strip().lower()
    if engine in VALID_DESKTOP_VOICE_ENGINES:
        config["engine"] = engine

    funasr_mode = str(source.get("funasrMode", config["funasrMode"])).strip().lower()
    if funasr_mode in VALID_FUNASR_MODES:
        config["funasrMode"] = funasr_mode

    funasr_model = str(source.get("funasrModel", config["funasrModel"])).strip()
    if funasr_model in VALID_FUNASR_MODELS:
        config["funasrModel"] = funasr_model
    if config["engine"] == "funasr" and config["funasrMode"] in {"streaming", "candidate_streaming"}:
        config["funasrStreamingModel"] = DEFAULT_STREAMING_MODEL
    try:
        streaming_chunk_ms = int(source.get("funasrStreamingChunkMs", config["funasrStreamingChunkMs"]))
    except (TypeError, ValueError):
        streaming_chunk_ms = config["funasrStreamingChunkMs"]
    config["funasrStreamingChunkMs"] = max(100, min(1000, streaming_chunk_ms))
    baidu_dev_pid = str(source.get("baiduDevPid", config["baiduDevPid"])).strip()
    config["baiduDevPid"] = baidu_dev_pid or DEFAULT_BAIDU_DEV_PID

    semantic_reranker = str(source.get("semanticReranker", config["semanticReranker"])).strip().lower()
    if semantic_reranker in VALID_SEMANTIC_RERANKERS:
        config["semanticReranker"] = semantic_reranker
    semantic_model = str(source.get("semanticModel", config["semanticModel"])).strip()
    config["semanticModel"] = semantic_model or DEFAULT_BERT_RERANKER_MODEL

    punctuation_strategy = str(source.get("punctuationStrategy", config["punctuationStrategy"])).strip().lower()
    if punctuation_strategy in VALID_PUNCTUATION_STRATEGIES:
        config["punctuationStrategy"] = punctuation_strategy

    config["voiceCommands"] = bool(source.get("voiceCommands", config["voiceCommands"]))
    config["hotwords"] = str(source.get("hotwords", config["hotwords"])).strip()
    return config


def bridge_settings_from_desktop_config(config: dict) -> BridgeSettings:
    use_spoken_punctuation = config.get("punctuationStrategy") == "spoken"
    return BridgeSettings(
        filter_punctuation=use_spoken_punctuation,
        convert_spoken_punctuation=use_spoken_punctuation,
        enable_voice_commands=bool(config.get("voiceCommands", True)),
    )


def create_asr_engine(config: dict, model_path: Path) -> StreamingASREngine:
    if config["engine"] == "vosk":
        return VoskEngine(model_path)
    if config["engine"] == "baidu":
        return BaiduSpeechEngine(dev_pid=config.get("baiduDevPid", DEFAULT_BAIDU_DEV_PID))
    if config.get("funasrMode") == "candidate_streaming":
        return FunASRCandidateStreamingEngine(
            DEFAULT_STREAMING_MODEL,
            hotwords=config.get("hotwords", ""),
            target_chunk_ms=config.get("funasrStreamingChunkMs", 600),
            semantic_reranker=config.get("semanticReranker", "bert"),
            semantic_model=config.get("semanticModel", DEFAULT_BERT_RERANKER_MODEL),
        )
    if config.get("funasrMode") == "streaming":
        return FunASRStreamingEngine(
            DEFAULT_STREAMING_MODEL,
            hotwords=config.get("hotwords", ""),
            target_chunk_ms=config.get("funasrStreamingChunkMs", 600),
        )
    return FunASROfflineEngine(
        model_name=config["funasrModel"],
        punctuation_strategy=config["punctuationStrategy"],
        hotwords=config.get("hotwords", ""),
    )


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
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        text_agent: TextAgentManager | None = None,
        typing_stats: TypingStats | None = None,
        input_gate: InputGate | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.token = token
        self.text_agent = text_agent
        self.typing_stats = typing_stats
        self.input_gate = input_gate
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
        app = create_app(
            self.token,
            text_agent=self.text_agent,
            typing_stats=self.typing_stats,
            input_gate=self.input_gate,
        )
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


class TextAgentHotkeyThread(threading.Thread):
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(
        self,
        callback,
        *,
        hotkey_id: int = 0x4641,
        virtual_key: int = 0x20,
        modifiers: int | None = None,
        label: str = "Ctrl+Alt+Space",
    ) -> None:
        super().__init__(daemon=True)
        self.callback = callback
        self.hotkey_id = hotkey_id
        self.virtual_key = virtual_key
        self.modifiers = self.MOD_CONTROL | self.MOD_ALT if modifiers is None else modifiers
        self.label = label
        self.thread_id: int | None = None
        self.ready = threading.Event()
        self.error: str | None = None
        self.stop_event = threading.Event()

    def run(self) -> None:
        if sys.platform != "win32":
            self.error = "Global hotkeys are only supported on Windows."
            self.ready.set()
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, self.hotkey_id, self.modifiers, self.virtual_key):
            self.error = f"RegisterHotKey failed: {ctypes.get_last_error()}"
            self.ready.set()
            return
        self.ready.set()
        msg = wintypes.MSG()
        try:
            while not self.stop_event.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == self.WM_HOTKEY and msg.wParam == self.hotkey_id:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, self.hotkey_id)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread_id is not None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)


class DesktopVoiceThread(threading.Thread):
    def __init__(
        self,
        model_path: Path,
        settings: BridgeSettings,
        config: dict,
        typing_stats: TypingStats | None = None,
        input_gate: InputGate | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.model_path = model_path
        self.settings = settings
        self.config = normalize_desktop_voice_config(config)
        self.typing_stats = typing_stats
        self.input_gate = input_gate
        self.session = self._create_input_session()
        self.asr_engine: StreamingASREngine | None = None
        self.punctuation_engine: PunctuationEngine | None = None
        self.audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=32)
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.RLock()
        self.audio_drop_count = 0
        self.audio_total_drop_count = 0
        self.error: str | None = None
        self.status = "STARTING"
        self.endpoint_status: dict = {}
        self.committed_text = ""
        self.pending_partial_text = ""
        self.composition_text = ""
        self.committed_partial_text = ""
        self.composition_tail_chars = 6
        self.latest_rescore_utterance_id = 0

    def snapshot(self) -> dict:
        with self.lock:
            running = self.is_alive() and self.error is None and not self.stop_event.is_set()
            return {
                "running": running,
                "paused": self.pause_event.is_set(),
                "status": self.status,
                "error": self.error,
                "modelPath": str(self.model_path),
                "engine": self.config["engine"],
                "funasrMode": self.config["funasrMode"],
                "funasrModel": self.config["funasrModel"],
                "baiduDevPid": self.config.get("baiduDevPid", DEFAULT_BAIDU_DEV_PID),
                "activeModel": self._active_model_name(),
                "finalRescoreModel": self._final_rescore_model_name(),
                "streamingChunkMs": self.config.get("funasrStreamingChunkMs", 600),
                "endpoint": dict(self.endpoint_status),
            }

    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status

    def set_error(self, message: str) -> None:
        with self.lock:
            self.error = message
            self.status = "ERROR"

    def run(self) -> None:
        try:
            self._run_recognizer()
        except Exception as exc:
            self.set_error(str(exc))
            self.ready.set()
        finally:
            if self._uses_ime_composition():
                self._clear_composition()
                self._reset_streaming_text_state()
            self.session.reset()
            if self.asr_engine is not None:
                self.asr_engine.close()
            if self.punctuation_engine is not None:
                self.punctuation_engine.close()
            if self.error is None:
                self.set_status("STOPPED")

    def _run_recognizer(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            self.set_error(f"Missing desktop voice dependency: {exc}")
            self.ready.set()
            return

        try:
            self.set_status(self._loading_status())
            self.asr_engine = create_asr_engine(self.config, self.model_path)
            self.asr_engine.start()
            self.punctuation_engine = PunctuationEngine(self.config["punctuationStrategy"])
            self.punctuation_engine.start()
        except Exception as exc:
            self.set_error(str(exc))
            self.ready.set()
            return

        sample_rate = 16000
        blocksize = 8000 if self.config["engine"] == "vosk" else 1600

        def audio_callback(indata, frames, time_info, status) -> None:
            if status:
                self.set_status(f"AUDIO WARNING: {status}")
            try:
                self.audio_queue.put_nowait(bytes(indata))
            except queue.Full:
                self._record_audio_drop()

        self.set_status("LISTENING")
        self.ready.set()
        endpoint_detector = EndpointDetector(
            EndpointConfig(
                sample_rate=sample_rate,
                frame_ms=max(1, int(blocksize * 1000 / sample_rate)),
            )
        )

        with sd.RawInputStream(
            samplerate=sample_rate,
            blocksize=blocksize,
            dtype="int16",
            channels=1,
            callback=audio_callback,
        ):
            while not self.stop_event.is_set():
                if self._input_paused():
                    self._discard_input_gate_audio()
                    endpoint_detector.reset()
                    time.sleep(0.1)
                    continue
                self._poll_asr_events()
                try:
                    data = self.audio_queue.get(timeout=0.1)
                except queue.Empty:
                    self._poll_asr_events()
                    continue

                if self.pause_event.is_set():
                    self._discard_paused_audio()
                    endpoint_detector.reset()
                    continue

                if self.config["engine"] == "vosk":
                    drops = self._consume_audio_drops()
                    if drops:
                        log(f"[endpoint] audio queue dropped {drops} frame(s) while using vosk")
                    self._handle_asr_events(self.asr_engine.accept_audio(data))
                    continue

                drops = self._consume_audio_drops()
                if drops:
                    drop_decision = endpoint_detector.handle_dropped_frames(drops)
                    self._update_endpoint_status(drop_decision)
                    log(f"[endpoint] audio queue dropped {drops} frame(s), state={drop_decision.state}")
                    if drop_decision.reset_asr:
                        self._handle_endpoint_reset(drop_decision)
                        continue

                decision = endpoint_detector.process(data)
                self._update_endpoint_status(decision)
                self._handle_endpoint_decision(decision)

    def _handle_endpoint_decision(self, decision: EndpointDecision) -> None:
        if decision.started:
            log(
                "[endpoint] speech start "
                f"snr={decision.features.snr_db:.1f}dB noise={decision.features.noise_rms:.1f} "
                f"rms={decision.features.rms:.1f}"
            )

        for chunk in decision.frames:
            self._handle_asr_events(self.asr_engine.accept_audio(chunk))

        if not decision.endpoint:
            return

        log(
            "[endpoint] speech end "
            f"reason={decision.reason} snr={decision.features.snr_db:.1f}dB "
            f"noise={decision.features.noise_rms:.1f} dropped={decision.dropped_frames}"
        )
        if decision.too_short or decision.reset_asr:
            self._handle_endpoint_reset(decision)
            return

        self.set_status("RECOGNIZING")
        self._handle_asr_events(self.asr_engine.finalize())
        self.set_status("LISTENING")

    def _handle_endpoint_reset(self, decision: EndpointDecision) -> None:
        if self._uses_ime_composition():
            self._discard_streaming_partial()
        if self.asr_engine is not None:
            self.asr_engine.reset()
        self.set_status("LISTENING")
        log(f"[endpoint] reset ASR reason={decision.reason}")

    def _update_endpoint_status(self, decision: EndpointDecision) -> None:
        with self.lock:
            self.endpoint_status = {
                "state": decision.state,
                "reason": decision.reason,
                "noiseRms": round(decision.features.noise_rms, 2),
                "rms": round(decision.features.rms, 2),
                "snrDb": round(decision.features.snr_db, 2),
                "dropCount": self.audio_drop_count,
                "totalDropCount": self.audio_total_drop_count,
            }

    def _record_audio_drop(self) -> None:
        with self.lock:
            self.audio_drop_count += 1
            self.audio_total_drop_count += 1

    def _consume_audio_drops(self) -> int:
        with self.lock:
            drops = self.audio_drop_count
            self.audio_drop_count = 0
            return drops

    def _loading_status(self) -> str:
        if self.config["engine"] == "vosk":
            return "LOADING MODEL"
        if self.config["engine"] == "baidu":
            return "LOADING BAIDU ASR"
        if self.config.get("funasrMode") == "candidate_streaming":
            return "LOADING FUNASR CANDIDATE STREAMING"
        if self.config.get("funasrMode") == "streaming":
            return "LOADING FUNASR STREAMING"
        return "LOADING FUNASR"

    def _active_model_name(self) -> str:
        if self.config["engine"] == "vosk":
            return "vosk"
        if self.config["engine"] == "baidu":
            return f"baidu-dev-pid-{self.config.get('baiduDevPid', DEFAULT_BAIDU_DEV_PID)}"
        if self.config.get("funasrMode") in {"streaming", "candidate_streaming"}:
            return DEFAULT_STREAMING_MODEL
        return self.config["funasrModel"]

    def _final_rescore_model_name(self) -> str:
        if self.config["engine"] == "funasr" and self.config.get("funasrMode") == "streaming":
            return "iic/SenseVoiceSmall"
        if self.config["engine"] == "funasr" and self.config.get("funasrMode") == "candidate_streaming":
            if self.config.get("semanticReranker") == "bert":
                return self.config.get("semanticModel", DEFAULT_BERT_RERANKER_MODEL)
            return "candidate heuristic reranker"
        return ""

    def _discard_paused_audio(self) -> None:
        if self._uses_ime_composition():
            self._discard_streaming_partial()
        self.pending_partial_text = ""
        self.committed_text = ""
        self._reset_streaming_text_state()
        self.session.reset()
        if self.asr_engine is not None:
            self.asr_engine.reset()
        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def _handle_asr_events(self, events: list[ASREvent]) -> None:
        if self._input_paused():
            self._discard_input_gate_audio()
            return
        if self._uses_ime_composition():
            self._handle_ime_asr_events(events)
            return

        for event in events:
            if event.type == "error":
                self.set_error(event.error or event.text or "ASR engine error")
                continue
            if event.type == "partial":
                partial = self._select_partial(event.text)
                self.pending_partial_text = partial
                self.session.sync_state(append_recognized_text(self.committed_text, partial), self.settings)
                continue
            if event.type == "final":
                text = event.text
                if self.punctuation_engine is not None:
                    text = self.punctuation_engine.apply_final(text)
                self.pending_partial_text = ""
                if text:
                    self.committed_text = append_recognized_text(self.committed_text, text)
                    self.session.sync_state(self.committed_text, self.settings)
                    self._reset_virtual_input_window_if_needed()

    def _select_partial(self, new_text: str) -> str:
        old = self.pending_partial_text
        if not old:
            return new_text
        if new_text.startswith(old):
            return new_text
        if len(new_text) + 2 < len(old):
            return old
        return new_text

    def _uses_ime_composition(self) -> bool:
        return self.config["engine"] == "funasr" and self.config.get("funasrMode") in {
            "streaming",
            "candidate_streaming",
        }

    def _handle_ime_asr_events(self, events: list[ASREvent]) -> None:
        if self._input_paused():
            self._discard_input_gate_audio()
            return
        for event in events:
            if event.type == "error":
                self._discard_streaming_partial()
                self._reset_streaming_text_state()
                self.set_error(event.error or event.text or "ASR engine error")
                continue
            if event.type == "partial":
                self._handle_streaming_virtual_partial(event)
                continue
            if event.type == "final":
                if event.source == "final_rescore":
                    self._handle_streaming_virtual_rescore(event)
                    continue
                self._handle_streaming_virtual_final(event)

    def _replace_composition(self, text: str) -> None:
        old = self.composition_text
        new = text or ""
        prefix_len = self._common_prefix_len(old, new)
        delete_count = len(old) - prefix_len
        append_text = new[prefix_len:]

        if delete_count:
            send_backspace_chunks(delete_count)
        if append_text:
            type_text(append_text)
            self._record_inserted_text(append_text)
        self.composition_text = new

    def _clear_composition(self) -> None:
        if self.composition_text:
            send_backspace_chunks(len(self.composition_text))
            self.composition_text = ""

    def _handle_streaming_virtual_partial(self, event: ASREvent) -> None:
        partial = event.text or ""
        if not partial:
            return
        self.pending_partial_text = partial
        self.session.sync_state(append_recognized_text(self.committed_text, partial), self.settings)

    def _handle_streaming_virtual_final(self, event: ASREvent) -> None:
        text = event.text
        if event.source == "streaming_final" and event.utterance_id:
            self.latest_rescore_utterance_id = event.utterance_id
        if self.punctuation_engine is not None:
            text = self.punctuation_engine.apply_final(text)

        if text:
            self.committed_text = append_recognized_text(self.committed_text, text)
            self.pending_partial_text = ""
            self.session.sync_state(self.committed_text, self.settings)
            self._reset_virtual_input_window_if_needed()
            return

        if self.pending_partial_text:
            self.committed_text = append_recognized_text(self.committed_text, self.pending_partial_text)
            self.pending_partial_text = ""
            self._reset_virtual_input_window_if_needed()

    def _handle_streaming_virtual_rescore(self, event: ASREvent) -> None:
        if event.utterance_id and event.utterance_id != self.latest_rescore_utterance_id:
            return
        original_text = event.stable_text or ""
        text = event.text
        if self.punctuation_engine is not None:
            text = self.punctuation_engine.apply_final(text)
        if not original_text or not text:
            return
        if not self.committed_text.endswith(original_text):
            return
        prefix = self.committed_text[: -len(original_text)]
        next_committed_text = append_recognized_text(prefix, text) if prefix else text
        if next_committed_text == self.committed_text:
            return
        self.committed_text = next_committed_text
        self.pending_partial_text = ""
        self.session.sync_state(self.committed_text, self.settings)
        self._reset_virtual_input_window_if_needed()

    def _discard_streaming_partial(self) -> None:
        self._clear_composition()
        if self.pending_partial_text:
            self.session.sync_state(self.committed_text, self.settings)
            self.pending_partial_text = ""

    def _handle_streaming_partial(self, event: ASREvent) -> None:
        full = render_text(event.text, self.settings)
        stable = render_text(event.stable_text, self.settings)
        if not full:
            return

        committed_target = stable if full.startswith(stable) else ""
        if len(full) > self.composition_tail_chars:
            forced_target = full[:-self.composition_tail_chars]
            if len(forced_target) > len(committed_target):
                committed_target = forced_target

        if self.committed_partial_text and not full.startswith(self.committed_partial_text):
            self._replace_composition(full[-self.composition_tail_chars :])
            return

        if len(committed_target) < len(self.committed_partial_text):
            committed_target = self.committed_partial_text

        newly_committed = committed_target[len(self.committed_partial_text) :]
        if newly_committed:
            self._clear_composition()
            type_text(newly_committed)
            self._record_inserted_text(newly_committed)
            self.committed_partial_text = committed_target

        composition_target = full[len(self.committed_partial_text) :]
        if len(composition_target) > self.composition_tail_chars:
            composition_target = composition_target[-self.composition_tail_chars :]
        self._replace_composition(composition_target)

    def _handle_streaming_final(self, event: ASREvent) -> None:
        text = event.text
        if self.punctuation_engine is not None:
            text = self.punctuation_engine.apply_final(text)
        final_text = render_text(text, self.settings)

        self._clear_composition()
        if not final_text:
            self._reset_streaming_text_state()
            return

        if not self.committed_partial_text:
            final_session = self._create_input_session()
            final_session.sync_state(final_text, self.settings)
        elif final_text.startswith(self.committed_partial_text):
            remaining = final_text[len(self.committed_partial_text) :]
            if remaining:
                final_session = self._create_input_session()
                final_session.sync_state(remaining, self.settings)

        self._reset_streaming_text_state()

    def _common_prefix_len(self, left: str, right: str) -> int:
        limit = min(len(left), len(right))
        index = 0
        while index < limit and left[index] == right[index]:
            index += 1
        return index

    def _reset_streaming_text_state(self) -> None:
        self.committed_partial_text = ""
        self.composition_text = ""
        self.latest_rescore_utterance_id = 0

    def _reset_virtual_input_window_if_needed(self) -> None:
        if len(self.committed_text) < DESKTOP_VOICE_VIRTUAL_RESET_CHARS:
            return
        self.committed_text = ""
        self.pending_partial_text = ""
        self.session.reset()

    def _poll_asr_events(self) -> None:
        if self.asr_engine is None:
            return
        if self._input_paused():
            return
        self._handle_asr_events(self.asr_engine.poll_events())

    def _input_paused(self) -> bool:
        return self.input_gate is not None and self.input_gate.is_paused()

    def _discard_input_gate_audio(self) -> None:
        if self._uses_ime_composition():
            self._discard_streaming_partial()
        self.pending_partial_text = ""
        self.committed_text = ""
        self._reset_streaming_text_state()
        self.session.reset()
        if self.asr_engine is not None:
            self.asr_engine.reset()
        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def resume_input_gate(self) -> None:
        self._discard_input_gate_audio()

    def _record_inserted_text(self, text: str) -> None:
        if self.typing_stats is not None:
            self.typing_stats.record(text, "computer")

    def _create_input_session(self) -> FlowInputSession:
        try:
            return FlowInputSession(self._record_inserted_text)
        except TypeError:
            return FlowInputSession()

    def stop(self) -> None:
        self.stop_event.set()

    def pause(self) -> None:
        self.pause_event.set()
        self.set_status("PAUSED")
        self._discard_paused_audio()

    def resume(self) -> None:
        self._discard_paused_audio()
        self.pause_event.clear()
        self.set_status("LISTENING")


class DesktopApi:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.lan_ip = get_lan_ip()
        self.page_version = str(int(time.time()))
        self.token = secrets.token_urlsafe(12)
        self.port = "8787"
        self.server_thread: BridgeServerThread | None = None
        self.desktop_voice_thread: DesktopVoiceThread | None = None
        self.input_gate = InputGate()
        self.typing_stats = TypingStats(
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FlowBridge" / "typing_stats.json"
        )
        self.text_agent = TextAgentManager(
            copy_callback=copy_text_to_clipboard,
            insert_callback=type_text,
            history_path=Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FlowBridge" / "text_agent_sessions.jsonl",
            trigger_chars=80,
        )
        self.text_agent_style = "meeting_notes"
        self.text_agent_hotkey_thread: TextAgentHotkeyThread | None = None
        self.input_gate_hotkey_thread: TextAgentHotkeyThread | None = None
        self.desktop_voice_config = normalize_desktop_voice_config(None)
        self.desktop_voice_settings = bridge_settings_from_desktop_config(self.desktop_voice_config)
        self.window: webview.Window | None = None
        self.agent_window: webview.Window | None = None
        self.input_toast_window = None
        self._agent_render = None
        self._input_toast_show = None
        self.maximized = False

    def _url(self) -> str:
        return f"http://{self.lan_ip}:{self.port}/?token={self.token}&v={self.page_version}"

    def _running(self) -> bool:
        return self.server_thread is not None and self.server_thread.error is None

    def _desktop_voice_running(self) -> bool:
        return (
            self.desktop_voice_thread is not None
            and self.desktop_voice_thread.is_alive()
            and self.desktop_voice_thread.error is None
            and not self.desktop_voice_thread.stop_event.is_set()
        )

    def _desktop_voice_settings_snapshot(self) -> dict:
        return dict(self.desktop_voice_config)

    def _desktop_voice_active_model_name(self) -> str:
        if self.desktop_voice_config["engine"] == "vosk":
            return "vosk"
        if self.desktop_voice_config["engine"] == "baidu":
            return f"baidu-dev-pid-{self.desktop_voice_config.get('baiduDevPid', DEFAULT_BAIDU_DEV_PID)}"
        if self.desktop_voice_config["funasrMode"] in {"streaming", "candidate_streaming"}:
            return DEFAULT_STREAMING_MODEL
        return self.desktop_voice_config["funasrModel"]
    def _desktop_voice_final_rescore_model_name(self) -> str:
        if self.desktop_voice_config["engine"] != "funasr":
            return ""
        if self.desktop_voice_config["funasrMode"] == "streaming":
            return "iic/SenseVoiceSmall"
        if self.desktop_voice_config["funasrMode"] == "candidate_streaming":
            if self.desktop_voice_config.get("semanticReranker") == "bert":
                return self.desktop_voice_config.get("semanticModel", DEFAULT_BERT_RERANKER_MODEL)
            return "candidate heuristic reranker"
        return ""

    def _result(self, message: str = "") -> dict:
        return {"state": self.get_state(), "message": message}

    def get_agent_float_state(self) -> dict:
        return {
            "textAgent": self.text_agent.get_float_state(),
            "textAgentHotkey": {
                "registered": self.text_agent_hotkey_thread is not None and self.text_agent_hotkey_thread.error is None,
                "error": self.text_agent_hotkey_thread.error if self.text_agent_hotkey_thread is not None else None,
                "label": "Ctrl+Alt+Space",
            },
            "inputGate": self.input_gate.snapshot(),
            "inputGateHotkey": {
                "registered": self.input_gate_hotkey_thread is not None and self.input_gate_hotkey_thread.error is None,
                "error": self.input_gate_hotkey_thread.error if self.input_gate_hotkey_thread is not None else None,
                "label": "Alt+M",
            },
        }

    def get_state(self) -> dict:
        with self.lock:
            running = self._running()
            voice_snapshot = (
                self.desktop_voice_thread.snapshot()
                if self.desktop_voice_thread is not None
                else {
                    "running": False,
                    "paused": False,
                    "status": "STOPPED",
                    "error": None,
                    "modelPath": str(desktop_voice_model_path()),
                    "engine": self.desktop_voice_config["engine"],
                    "funasrMode": self.desktop_voice_config["funasrMode"],
                    "funasrModel": self.desktop_voice_config["funasrModel"],
                    "baiduDevPid": self.desktop_voice_config.get("baiduDevPid", DEFAULT_BAIDU_DEV_PID),
                    "activeModel": self._desktop_voice_active_model_name(),
                    "finalRescoreModel": self._desktop_voice_final_rescore_model_name(),
                    "streamingChunkMs": self.desktop_voice_config.get("funasrStreamingChunkMs", 600),
                }
            )
            return {
                "running": running,
                "token": self.token,
                "ip": self.lan_ip,
                "port": self.port,
                "url": self._url(),
                "status": "SERVICE STARTED" if running else "SERVICE STOPPED",
                "desktopVoice": voice_snapshot,
                "desktopVoiceSettings": self._desktop_voice_settings_snapshot(),
                "textAgent": self.text_agent.get_state(),
                "textAgentStyle": self.text_agent_style,
                "textAgentHotkey": {
                    "registered": self.text_agent_hotkey_thread is not None and self.text_agent_hotkey_thread.error is None,
                    "error": self.text_agent_hotkey_thread.error if self.text_agent_hotkey_thread is not None else None,
                    "label": "Ctrl+Alt+Space",
                },
                "inputGate": self.input_gate.snapshot(),
                "inputGateHotkey": {
                    "registered": self.input_gate_hotkey_thread is not None and self.input_gate_hotkey_thread.error is None,
                    "error": self.input_gate_hotkey_thread.error if self.input_gate_hotkey_thread is not None else None,
                    "label": "Alt+M",
                },
                "typingStats": self.typing_stats.snapshot(),
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

    def _start_service_locked(self) -> tuple[BridgeServerThread | None, str | None]:
        if self._running():
            return None, "Service is already running."
        try:
            port = int(self.port)
            if port <= 0 or port > 65535:
                raise ValueError
        except ValueError:
            return None, "Port must be between 1 and 65535."

        thread = BridgeServerThread("0.0.0.0", port, self.token, self.text_agent, self.typing_stats, self.input_gate)
        self.server_thread = thread
        thread.start()
        return thread, None

    def start_service(self) -> dict:
        with self.lock:
            thread, error = self._start_service_locked()
            if error:
                return self._result(error)

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

    def refresh_connection(self) -> dict:
        with self.lock:
            thread = self.server_thread
            self.server_thread = None
        if thread is not None:
            thread.stop()
            thread.join(timeout=2)

        with self.lock:
            self.lan_ip = get_lan_ip()
            self.page_version = str(int(time.time()))
            thread, error = self._start_service_locked()
            if error:
                return self._result(error)

        thread.ready.wait(timeout=4)

        with self.lock:
            if thread.error:
                self.server_thread = None
                return self._result(f"Failed to refresh connection: {thread.error}")
            return self._result("Connection refreshed. Scan the updated QR code.")

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

    def set_text_agent_mode(self, value: dict | bool) -> dict:
        enabled = bool(value.get("enabled")) if isinstance(value, dict) else bool(value)
        self.text_agent.set_mode(enabled)
        return self._result("Text agent mode enabled." if enabled else "Text agent mode disabled.")

    def set_text_agent_style(self, value: str) -> dict:
        self.text_agent_style = str(value or "meeting_notes")
        return self._result("Text agent style updated.")

    def start_text_agent_recording(self, value: dict | None = None) -> dict:
        payload = value if isinstance(value, dict) else {}
        style = str(payload.get("style", self.text_agent_style))
        self.text_agent_style = style
        session = self.text_agent.start(style)
        return self._result(f"Text agent recording started: {session.id}")

    def stop_text_agent_recording(self) -> dict:
        try:
            session = self.text_agent.stop(copy=True, insert=False)
            return self._result(f"Text agent copied {len(session.final_text or session.draft_text)} chars to clipboard.")
        except Exception as exc:
            return self._result(f"Text agent stop failed: {exc}")

    def pause_text_agent_recording(self) -> dict:
        try:
            self.text_agent.pause()
            return self._result("Text agent paused. Mobile text will use normal injection.")
        except Exception as exc:
            return self._result(f"Text agent pause failed: {exc}")

    def resume_text_agent_recording(self) -> dict:
        try:
            self.text_agent.resume()
            return self._result("Text agent recording resumed.")
        except Exception as exc:
            return self._result(f"Text agent resume failed: {exc}")

    def toggle_text_agent_recording(self) -> dict:
        try:
            session = self.text_agent.toggle_recording(self.text_agent_style)
            if session.status == "recording":
                return self._result("Text agent recording started.")
            return self._result("Text agent final text copied to clipboard.")
        except Exception as exc:
            return self._result(f"Text agent hotkey failed: {exc}")

    def rerun_text_agent(self, value: dict | None = None) -> dict:
        payload = value if isinstance(value, dict) else {}
        style = payload.get("style")
        if isinstance(style, str) and style:
            self.text_agent_style = style
        try:
            self.text_agent.rerun(style if isinstance(style, str) else None)
            return self._result("Text agent result refreshed.")
        except Exception as exc:
            return self._result(f"Text agent refresh failed: {exc}")

    def copy_text_agent_result(self) -> dict:
        try:
            self.text_agent.copy_result()
            return self._result("Text agent result copied.")
        except Exception as exc:
            return self._result(f"Copy failed: {exc}")

    def copy_partial_text_agent_notes(self) -> dict:
        try:
            markdown = self.text_agent.copy_partial_notes()
            return self._result(f"Copied {len(markdown)} characters of partial meeting notes.")
        except Exception as exc:
            return self._result(f"Copy partial notes failed: {exc}")

    def insert_text_agent_result(self) -> dict:
        try:
            self.text_agent.insert_result()
            return self._result("Text agent result inserted.")
        except Exception as exc:
            return self._result(f"Insert failed: {exc}")

    def show_main_window(self) -> dict:
        if self.window is not None:
            self.window.show()
            self.window.restore()
        return self._result()

    def toggle_input_pause(self) -> dict:
        paused = self.input_gate.toggle()
        if not paused:
            thread = self.desktop_voice_thread
            if thread is not None:
                thread.resume_input_gate()
        self.show_input_gate_toast(paused)
        return self._result("Input paused." if paused else "Input resumed.")

    def set_input_pause(self, value: bool) -> dict:
        paused = self.input_gate.set_paused(bool(value))
        if not paused:
            thread = self.desktop_voice_thread
            if thread is not None:
                thread.resume_input_gate()
        self.show_input_gate_toast(paused)
        return self._result("Input paused." if paused else "Input resumed.")

    def show_input_gate_toast(self, paused: bool) -> None:
        toast_window = self.input_toast_window
        toast_show = self._input_toast_show
        if toast_window is None or toast_show is None:
            log("[desktop] input toast is not ready")
            return
        try:
            from System import Action

            def show() -> None:
                toast_show(bool(paused))

            if getattr(toast_window, "InvokeRequired", False):
                toast_window.BeginInvoke(Action(show))
            else:
                show()
        except Exception as exc:
            log(f"[desktop] input toast failed: {exc}")

    def show_agent_float(self) -> None:
        agent_window = self.agent_window
        if agent_window is None:
            return
        try:
            from System import Action

            def show() -> None:
                agent_window.Show()
                agent_window.Activate()
                if self._agent_render is not None:
                    self._agent_render()

            if agent_window.InvokeRequired:
                agent_window.BeginInvoke(Action(show))
            else:
                show()
        except Exception as exc:
            log(f"[desktop] show agent float failed: {exc}")

    def start_desktop_voice(self) -> dict:
        with self.lock:
            if self._desktop_voice_running():
                return self._result("Desktop voice is already listening.")
            thread = DesktopVoiceThread(
                desktop_voice_model_path(),
                self.desktop_voice_settings,
                self.desktop_voice_config,
                self.typing_stats,
                self.input_gate,
            )
            self.desktop_voice_thread = thread
            thread.start()

        thread.ready.wait(timeout=8)

        with self.lock:
            if thread.error:
                return self._result(f"Failed to start desktop voice: {thread.error}")
            return self._result("Desktop voice started.")

    def stop_desktop_voice(self) -> dict:
        with self.lock:
            thread = self.desktop_voice_thread
            self.desktop_voice_thread = None
        if thread is not None:
            thread.stop()
            thread.join(timeout=2)
        return self._result("Desktop voice stopped.")

    def pause_desktop_voice(self) -> dict:
        with self.lock:
            thread = self.desktop_voice_thread
            if thread is None or not self._desktop_voice_running():
                return self._result("Desktop voice is not running.")
            thread.pause()
            return self._result("Desktop voice paused.")

    def resume_desktop_voice(self) -> dict:
        with self.lock:
            thread = self.desktop_voice_thread
            if thread is None or not self._desktop_voice_running():
                return self._result("Desktop voice is not running.")
            thread.resume()
            return self._result("Desktop voice resumed.")

    def set_desktop_voice_settings(self, value: dict) -> dict:
        with self.lock:
            previous_engine = self.desktop_voice_config["engine"]
            previous_mode = self.desktop_voice_config["funasrMode"]
            previous_model = self.desktop_voice_config["funasrModel"]
            previous_chunk_ms = self.desktop_voice_config.get("funasrStreamingChunkMs", 600)
            previous_baidu_dev_pid = self.desktop_voice_config.get("baiduDevPid", DEFAULT_BAIDU_DEV_PID)
            previous_hotwords = self.desktop_voice_config.get("hotwords", "")
            previous_reranker = self.desktop_voice_config.get("semanticReranker", "bert")
            previous_semantic_model = self.desktop_voice_config.get("semanticModel", DEFAULT_BERT_RERANKER_MODEL)
            self.desktop_voice_config = normalize_desktop_voice_config(value)
            next_settings = bridge_settings_from_desktop_config(self.desktop_voice_config)
            self.desktop_voice_settings.filter_punctuation = next_settings.filter_punctuation
            self.desktop_voice_settings.convert_spoken_punctuation = next_settings.convert_spoken_punctuation
            self.desktop_voice_settings.enable_voice_commands = next_settings.enable_voice_commands
            needs_restart = self._desktop_voice_running() and (
                previous_engine != self.desktop_voice_config["engine"]
                or previous_mode != self.desktop_voice_config["funasrMode"]
                or previous_model != self.desktop_voice_config["funasrModel"]
                or previous_chunk_ms != self.desktop_voice_config.get("funasrStreamingChunkMs", 600)
                or previous_baidu_dev_pid != self.desktop_voice_config.get("baiduDevPid", DEFAULT_BAIDU_DEV_PID)
                or previous_hotwords != self.desktop_voice_config.get("hotwords", "")
                or previous_reranker != self.desktop_voice_config.get("semanticReranker", "bert")
                or previous_semantic_model != self.desktop_voice_config.get("semanticModel", DEFAULT_BERT_RERANKER_MODEL)
            )
            if needs_restart:
                return self._result("Settings saved. Restart desktop voice to apply model or hotword changes.")
            return self._result("Desktop voice settings updated.")

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
        if self.agent_window is not None:
            try:
                agent_window = self.agent_window
                if getattr(agent_window, "InvokeRequired", False):
                    from System import Action

                    agent_window.BeginInvoke(Action(agent_window.Close))
                else:
                    agent_window.Close()
            except Exception:
                pass
            self.agent_window = None
        if self.input_toast_window is not None:
            try:
                toast_window = self.input_toast_window
                if getattr(toast_window, "InvokeRequired", False):
                    from System import Action

                    toast_window.BeginInvoke(Action(toast_window.Close))
                else:
                    toast_window.Close()
            except Exception:
                pass
            self.input_toast_window = None
        if self.text_agent_hotkey_thread is not None:
            self.text_agent_hotkey_thread.stop()
            self.text_agent_hotkey_thread.join(timeout=2)
            self.text_agent_hotkey_thread = None
        if self.input_gate_hotkey_thread is not None:
            self.input_gate_hotkey_thread.stop()
            self.input_gate_hotkey_thread.join(timeout=2)
            self.input_gate_hotkey_thread = None
        self.stop_desktop_voice()
        self.stop_service()
        self.typing_stats.close()

    def start_hotkeys(self) -> None:
        if self.text_agent_hotkey_thread is None:
            def callback() -> None:
                self.show_agent_float()
                self.toggle_text_agent_recording()

            thread = TextAgentHotkeyThread(callback)
            self.text_agent_hotkey_thread = thread
            thread.start()
            thread.ready.wait(timeout=2)
            if thread.error:
                log(f"[text-agent] hotkey unavailable: {thread.error}")

        if self.input_gate_hotkey_thread is None:
            thread = TextAgentHotkeyThread(
                self.toggle_input_pause,
                hotkey_id=0x4642,
                virtual_key=0x4D,
                modifiers=TextAgentHotkeyThread.MOD_ALT,
                label="Alt+M",
            )
            self.input_gate_hotkey_thread = thread
            thread.start()
            thread.ready.wait(timeout=2)
            if thread.error:
                log(f"[input-gate] hotkey unavailable: {thread.error}")


def apply_window_chrome(window: webview.Window) -> None:
    if sys.platform != "win32":
        return
    try:
        import clr

        clr.AddReference("System.Drawing")
        from System.Drawing import Icon

        icon_path = Path(__file__).resolve().parent / "assets" / "flowvoice_hurricane_eye.ico"
        if icon_path.exists():
            window.native.Icon = Icon(str(icon_path))
    except Exception:
        pass

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


def create_native_agent_float(api: DesktopApi) -> object:
    import clr

    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")
    from System import Array
    from System.Drawing import Bitmap, Brushes, Color, Font, FontStyle, Graphics, Pen, PointF, Rectangle, Size, SolidBrush
    from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
    from System.Drawing.Imaging import ImageLockMode, PixelFormat
    from System.Drawing.Text import TextRenderingHint
    from System.Windows.Forms import (
        Form,
        FormBorderStyle,
        FormStartPosition,
        MouseButtons,
        Timer,
    )

    class BlendFunction(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_ubyte),
            ("BlendFlags", ctypes.c_ubyte),
            ("SourceConstantAlpha", ctypes.c_ubyte),
            ("AlphaFormat", ctypes.c_ubyte),
        ]

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]

    def rounded_path(x: float, y: float, width: float, height: float, radius: float):
        path = GraphicsPath()
        diameter = radius * 2
        path.AddArc(x, y, diameter, diameter, 180, 90)
        path.AddArc(x + width - diameter, y, diameter, diameter, 270, 90)
        path.AddArc(x + width - diameter, y + height - diameter, diameter, diameter, 0, 90)
        path.AddArc(x, y + height - diameter, diameter, diameter, 90, 90)
        path.CloseFigure()
        return path

    form = Form()
    form.Text = "FlowVoice Agent"
    form.ClientSize = Size(320, 178)
    form.FormBorderStyle = getattr(FormBorderStyle, "None")
    form.TopMost = True
    form.ShowInTaskbar = False
    form.StartPosition = FormStartPosition.CenterScreen
    state = {"recording": False, "paused": False, "last_preview": None}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    user32.ReleaseCapture.restype = wintypes.BOOL
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.UpdateLayeredWindow.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.POINT),
        wintypes.COLORREF,
        ctypes.POINTER(BlendFunction),
        wintypes.DWORD,
    ]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(BitmapInfo),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    status_font = Font("Microsoft YaHei UI", 8, FontStyle.Regular)
    preview_font = Font("Microsoft YaHei UI", 10, FontStyle.Bold)
    icon_font = Font("Segoe UI Symbol", 11, FontStyle.Bold)

    def wrap_latest_lines(graphics, text: str, max_width: float, max_lines: int = 3) -> list[str]:
        lines = []
        current = ""
        for char in text:
            candidate = f"{current}{char}"
            if current and graphics.MeasureString(candidate, preview_font).Width > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines[-max_lines:] or [""]

    def update_layered(bitmap: Bitmap) -> None:
        hwnd = ctypes.c_void_p(form.Handle.ToInt64())
        screen_dc = user32.GetDC(0)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap_info = BitmapInfo()
        bitmap_info.bmiHeader = BitmapInfoHeader(
            ctypes.sizeof(BitmapInfoHeader),
            320,
            -178,
            1,
            32,
            0,
            320 * 178 * 4,
            0,
            0,
            0,
            0,
        )
        pixels = ctypes.c_void_p()
        hbitmap = gdi32.CreateDIBSection(
            screen_dc,
            ctypes.byref(bitmap_info),
            0,
            ctypes.byref(pixels),
            None,
            0,
        )
        bitmap_data = bitmap.LockBits(
            Rectangle(0, 0, 320, 178),
            ImageLockMode.ReadOnly,
            PixelFormat.Format32bppPArgb,
        )
        try:
            ctypes.memmove(pixels, bitmap_data.Scan0.ToInt64(), 320 * 178 * 4)
        finally:
            bitmap.UnlockBits(bitmap_data)
        old_bitmap = gdi32.SelectObject(memory_dc, hbitmap)
        try:
            destination = wintypes.POINT(form.Left, form.Top)
            source = wintypes.POINT(0, 0)
            size = wintypes.SIZE(320, 178)
            blend = BlendFunction(0, 0, 255, 1)
            user32.UpdateLayeredWindow(
                hwnd,
                screen_dc,
                ctypes.byref(destination),
                ctypes.byref(size),
                memory_dc,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                2,
            )
        finally:
            gdi32.SelectObject(memory_dc, old_bitmap)
            gdi32.DeleteObject(hbitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(0, screen_dc)

    def render() -> None:
        snapshot = api.text_agent.get_float_state()
        recording = bool(snapshot["recording"])
        paused = bool(snapshot["paused"])
        polishing = bool(snapshot["polishing"])
        completed = bool(snapshot["completed"])
        state["recording"] = recording
        state["paused"] = paused

        status = "整理中" if polishing else "记录中" if recording else "已暂停" if paused else "已完成" if completed else "待机"
        text = snapshot["rawText"] or ("本次会议纪要已保存至剪贴板" if completed else "等待手机端输入原始文本")

        bitmap = Bitmap(320, 178, PixelFormat.Format32bppPArgb)
        graphics = Graphics.FromImage(bitmap)
        graphics.SmoothingMode = SmoothingMode.AntiAlias
        graphics.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
        graphics.Clear(Color.Transparent)
        try:
            bubble_path = rounded_path(8, 4, 304, 108, 24)
            bubble_brush = SolidBrush(Color.FromArgb(250, 255, 249))
            bubble_pen = Pen(Color.FromArgb(207, 224, 212), 1)
            graphics.FillPath(bubble_brush, bubble_path)
            graphics.DrawPath(bubble_pen, bubble_path)
            bubble_pen.Dispose()
            bubble_path.Dispose()

            tail_path = GraphicsPath()
            tail_path.AddPolygon(Array[PointF]([PointF(148, 105), PointF(172, 105), PointF(160, 120)]))
            graphics.FillPath(bubble_brush, tail_path)
            tail_path.Dispose()
            bubble_brush.Dispose()

            controls_path = rounded_path(104, 124, 112, 48, 20)
            graphics.FillPath(Brushes.Black, controls_path)
            controls_path.Dispose()

            primary_color = Color.FromArgb(224, 71, 71) if recording or paused else Color.FromArgb(32, 201, 117)
            primary_brush = SolidBrush(primary_color)
            graphics.FillEllipse(primary_brush, 114, 128, 40, 40)
            primary_brush.Dispose()
            graphics.FillEllipse(Brushes.White, 166, 128, 40, 40)

            status_brush = SolidBrush(Color.FromArgb(42, 111, 69))
            graphics.DrawString(status, status_font, status_brush, 22, 13)
            status_brush.Dispose()
            hide_pen = Pen(Color.FromArgb(80, 110, 92), 2)
            graphics.DrawLine(hide_pen, 282, 20, 296, 20)
            hide_pen.Dispose()

            lines = wrap_latest_lines(graphics, text, 270)
            text_brush = SolidBrush(Color.FromArgb(6, 16, 11))
            graphics.DrawString("\n".join(lines), preview_font, text_brush, 22, 37)
            text_brush.Dispose()
            primary_icon = "■" if recording or paused else "●"
            secondary_icon = "▶" if paused else "Ⅱ"
            graphics.DrawString(primary_icon, icon_font, Brushes.White, 125, 137)
            secondary_brush = SolidBrush(Color.FromArgb(30, 91, 56))
            graphics.DrawString(secondary_icon, icon_font, secondary_brush, 176, 137)
            secondary_brush.Dispose()
        finally:
            graphics.Dispose()
        update_layered(bitmap)
        bitmap.Dispose()

    def run_async(callback) -> None:
        threading.Thread(target=callback, daemon=True).start()

    def begin_drag(_sender, event) -> None:
        try:
            if event.Button != MouseButtons.Left:
                return
            x, y = event.X, event.Y
            if 274 <= x <= 306 and 8 <= y <= 34:
                form.Hide()
                return
            if 114 <= x <= 154 and 128 <= y <= 168:
                callback = api.stop_text_agent_recording if state["recording"] or state["paused"] else api.toggle_text_agent_recording
                run_async(callback)
                return
            if 166 <= x <= 206 and 128 <= y <= 168:
                if state["recording"] or state["paused"]:
                    callback = api.resume_text_agent_recording if state["paused"] else api.pause_text_agent_recording
                    run_async(callback)
                return
            if event.Y <= 112:
                run_async(api.show_main_window)
            user32.ReleaseCapture()
            user32.SendMessageW(ctypes.c_void_p(form.Handle.ToInt64()), 0x00A1, 2, 0)
        except Exception as exc:
            log(f"[desktop] agent mouse callback failed: {exc}\n{traceback.format_exc()}")

    form.MouseDown += begin_drag

    def refresh(_sender=None, _event=None) -> None:
        try:
            if form.Visible:
                render()
        except Exception as exc:
            log(f"[desktop] agent refresh failed: {exc}\n{traceback.format_exc()}")

    timer = Timer()
    timer.Interval = 500
    timer.Tick += refresh
    timer.Start()
    form.FormClosed += lambda _sender, _event: timer.Stop()
    form.Show()
    hwnd = ctypes.c_void_p(form.Handle.ToInt64())
    ex_style = user32.GetWindowLongPtrW(hwnd, -20)
    user32.SetWindowLongPtrW(hwnd, -20, ctypes.c_void_p(ex_style | 0x00080000))
    render()
    api._agent_render = render
    return form


def create_native_input_gate_toast(api: DesktopApi) -> object:
    import clr

    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")
    from System.Drawing import Color, Font, FontStyle, Point, Region, Size
    from System.Drawing.Drawing2D import GraphicsPath
    from System.Windows.Forms import Form, FormBorderStyle, FormStartPosition, Label, Panel, Screen, Timer

    form = Form()
    form.Text = "FlowVoice Input Status"
    form.ClientSize = Size(260, 58)
    form.FormBorderStyle = getattr(FormBorderStyle, "None")
    form.TopMost = True
    form.ShowInTaskbar = False
    form.StartPosition = FormStartPosition.Manual
    form.BackColor = Color.FromArgb(8, 16, 12)
    form.Opacity = 0.94

    def rounded_path(width: int, height: int, radius: int) -> GraphicsPath:
        path = GraphicsPath()
        diameter = radius * 2
        path.AddArc(0, 0, diameter, diameter, 180, 90)
        path.AddArc(width - diameter, 0, diameter, diameter, 270, 90)
        path.AddArc(width - diameter, height - diameter, diameter, diameter, 0, 90)
        path.AddArc(0, height - diameter, diameter, diameter, 90, 90)
        path.CloseFigure()
        return path

    def rounded_region(width: int, height: int, radius: int) -> Region:
        path = rounded_path(width, height, radius)
        try:
            return Region(path)
        finally:
            path.Dispose()

    form.Region = rounded_region(260, 58, 18)

    dot = Panel()
    dot.Size = Size(12, 12)
    dot.Location = Point(22, 23)
    dot.BackColor = Color.FromArgb(40, 245, 141)
    dot.Region = rounded_region(12, 12, 6)
    form.Controls.Add(dot)

    label = Label()
    label.AutoSize = False
    label.Location = Point(46, 14)
    label.Size = Size(190, 30)
    label.Font = Font("Microsoft YaHei UI", 12, FontStyle.Bold)
    label.ForeColor = Color.FromArgb(220, 255, 232)
    label.BackColor = Color.Transparent
    label.Text = "已开启语音输入"
    form.Controls.Add(label)

    hide_timer = Timer()
    hide_timer.Interval = 1450

    def hide(_sender=None, _event=None) -> None:
        hide_timer.Stop()
        form.Hide()

    hide_timer.Tick += hide

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    SW_SHOWNOACTIVATE = 4
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080

    def place_form() -> None:
        area = Screen.PrimaryScreen.WorkingArea
        form.Left = int(area.Left + (area.Width - form.Width) / 2)
        form.Top = int(area.Bottom - form.Height - 92)

    def show_toast(paused: bool) -> None:
        hide_timer.Stop()
        place_form()
        if paused:
            dot.BackColor = Color.FromArgb(215, 196, 122)
            label.ForeColor = Color.FromArgb(255, 238, 166)
            label.Text = "已暂停语音输入"
        else:
            dot.BackColor = Color.FromArgb(40, 245, 141)
            label.ForeColor = Color.FromArgb(220, 255, 232)
            label.Text = "已开启语音输入"
        if form.Visible:
            form.Hide()
        form.Show()
        user32.ShowWindow(ctypes.c_void_p(form.Handle.ToInt64()), SW_SHOWNOACTIVATE)
        hide_timer.Start()

    form.FormClosed += lambda _sender, _event: hide_timer.Stop()
    form.Show()
    hwnd = ctypes.c_void_p(form.Handle.ToInt64())
    ex_style = int(user32.GetWindowLongPtrW(hwnd, -20) or 0)
    user32.SetWindowLongPtrW(hwnd, -20, ctypes.c_void_p(ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW))
    form.Hide()
    api._input_toast_show = show_toast
    return form


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This program injects text with Windows SendInput and must run on Windows.")

    api = DesktopApi()
    page_url = ui_url()
    window = webview.create_window(
        "Flow Voice",
        page_url,
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

    def create_agent_window() -> None:
        if api.agent_window is not None:
            return
        try:
            log("[desktop] creating agent window")
            from System import Action

            def create_on_ui_thread() -> None:
                try:
                    api.agent_window = create_native_agent_float(api)
                    log("[desktop] native agent window shown")
                except Exception as exc:
                    log(f"[desktop] native agent window failed: {exc}\n{traceback.format_exc()}")

            if window.native.InvokeRequired:
                window.native.BeginInvoke(Action(create_on_ui_thread))
            else:
                create_on_ui_thread()
            log("[desktop] agent window creation scheduled")
        except Exception as exc:
            log(f"[desktop] agent window creation failed: {exc}")

    def create_input_toast_window() -> None:
        if api.input_toast_window is not None:
            return
        try:
            log("[desktop] creating input toast window")
            from System import Action

            def create_on_ui_thread() -> None:
                try:
                    api.input_toast_window = create_native_input_gate_toast(api)
                    log("[desktop] input toast window ready")
                except Exception as exc:
                    log(f"[desktop] input toast window failed: {exc}\n{traceback.format_exc()}")

            if window.native.InvokeRequired:
                window.native.BeginInvoke(Action(create_on_ui_thread))
            else:
                create_on_ui_thread()
        except Exception as exc:
            log(f"[desktop] input toast window creation failed: {exc}")

    def on_main_window_loaded() -> None:
        log("[desktop] main window loaded")
        apply_window_chrome(window)
        threading.Timer(0.8, create_agent_window).start()
        threading.Timer(0.2, create_input_toast_window).start()

    window.events.loaded += on_main_window_loaded
    window.events.closing += lambda: api.shutdown()
    api.start_hotkeys()
    log("[desktop] starting webview")
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
