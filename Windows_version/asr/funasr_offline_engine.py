from __future__ import annotations

import re

from .base import ASREvent, StreamingASREngine


TAG_PATTERN = re.compile(r"<\|[^>]+?\|>")
MULTI_SPACE_PATTERN = re.compile(r"\s+")
SAMPLE_RATE = 16000


def normalize_text(text: str) -> str:
    return MULTI_SPACE_PATTERN.sub(" ", TAG_PATTERN.sub("", text or "").strip()).strip()


def extract_text(result) -> str:
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            return normalize_text(str(first.get("text", "")))
    if isinstance(result, dict):
        return normalize_text(str(result.get("text", "")))
    return normalize_text(str(result or ""))


class FunASROfflineEngine(StreamingASREngine):
    def __init__(
        self,
        model_name: str = "iic/SenseVoiceSmall",
        punctuation_strategy: str = "spoken",
        hotwords: str = "",
    ) -> None:
        self.model_name = model_name
        self.punctuation_strategy = punctuation_strategy
        self.hotwords = hotwords.strip()
        self.model = None
        self.chunks: list[bytes] = []

    def start(self) -> None:
        from funasr import AutoModel

        model_kwargs = {"model": self.model_name, "disable_update": True}
        if self.model_name == "paraformer-zh":
            model_kwargs["vad_model"] = "fsmn-vad"
        try:
            self.model = AutoModel(**model_kwargs)
        except TypeError:
            model_kwargs.pop("disable_update", None)
            self.model = AutoModel(**model_kwargs)

    def accept_audio(self, pcm: bytes) -> list[ASREvent]:
        if pcm:
            self.chunks.append(pcm)
        return []

    def finalize(self) -> list[ASREvent]:
        if not self.chunks:
            return []
        if self.model is None:
            return [ASREvent(type="error", text="", error="FunASR offline model is not started.")]

        import numpy as np

        audio = np.frombuffer(b"".join(self.chunks), dtype=np.int16).astype(np.float32) / 32768.0
        self.chunks = []
        if audio.size == 0:
            return []

        generate_kwargs = {"input": audio, "batch_size_s": 60, "fs": SAMPLE_RATE}
        if self.hotwords:
            generate_kwargs["hotword"] = self.hotwords

        try:
            result = self.model.generate(**generate_kwargs)
        except TypeError:
            generate_kwargs.pop("fs", None)
            try:
                result = self.model.generate(**generate_kwargs)
            except TypeError:
                generate_kwargs.pop("hotword", None)
                result = self.model.generate(**generate_kwargs)

        text = extract_text(result)
        return [ASREvent(type="final", text=text)] if text else []

    def reset(self) -> None:
        self.chunks = []

    def close(self) -> None:
        self.reset()
        self.model = None
