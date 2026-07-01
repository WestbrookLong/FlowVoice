from __future__ import annotations

import json
import re
from pathlib import Path

from .base import ASREvent, StreamingASREngine
from .text_stability import split_stable_text


CJK_SPACE_PATTERN = re.compile(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])")
MULTI_SPACE_PATTERN = re.compile(r"\s+")
SAMPLE_RATE = 16000


def normalize_text(text: str) -> str:
    normalized = CJK_SPACE_PATTERN.sub("", str(text or "").strip())
    return MULTI_SPACE_PATTERN.sub(" ", normalized).strip()


class VoskEngine(StreamingASREngine):
    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self.model = None
        self.recognizer = None
        self.prev_partial = ""

    def start(self) -> None:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        if not self.model_path.exists():
            raise FileNotFoundError(f"Vosk model not found: {self.model_path}")
        SetLogLevel(-1)
        self.model = Model(str(self.model_path))
        self.recognizer = KaldiRecognizer(self.model, SAMPLE_RATE)
        self.recognizer.SetWords(False)

    def accept_audio(self, pcm: bytes) -> list[ASREvent]:
        if self.recognizer is None:
            return [ASREvent(type="error", text="", error="Vosk recognizer is not started.")]
        if self.recognizer.AcceptWaveform(pcm):
            text = normalize_text(json.loads(self.recognizer.Result()).get("text", ""))
            self.prev_partial = ""
            return [ASREvent(type="final", text=text)] if text else []

        text = normalize_text(json.loads(self.recognizer.PartialResult()).get("partial", ""))
        if not text:
            return []
        stable_text, unstable_text = split_stable_text(self.prev_partial, text)
        self.prev_partial = text
        return [
            ASREvent(
                type="partial",
                text=text,
                stable_text=stable_text,
                unstable_text=unstable_text,
            )
        ]

    def finalize(self) -> list[ASREvent]:
        if self.recognizer is None:
            return []
        text = normalize_text(json.loads(self.recognizer.FinalResult()).get("text", ""))
        self.prev_partial = ""
        return [ASREvent(type="final", text=text)] if text else []

    def reset(self) -> None:
        self.prev_partial = ""

    def close(self) -> None:
        self.reset()
        self.recognizer = None
        self.model = None
