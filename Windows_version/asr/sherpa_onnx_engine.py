from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import ASREvent, StreamingASREngine


SAMPLE_RATE = 16000
MODEL_NAME = "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30"


class SherpaOnnxStreamingEngine(StreamingASREngine):
    def __init__(self, model_dir: Path, num_threads: int = 2) -> None:
        self.model_dir = Path(model_dir)
        self.num_threads = num_threads
        self.recognizer = None
        self.stream = None
        self.last_partial = ""

    def start(self) -> None:
        try:
            import sherpa_onnx
        except Exception as exc:
            raise RuntimeError("Missing dependency: install sherpa-onnx for desktop ONNX voice.") from exc

        encoder = self.model_dir / "encoder.int8.onnx"
        decoder = self.model_dir / "decoder.onnx"
        joiner = self.model_dir / "joiner.int8.onnx"
        tokens = self.model_dir / "tokens.txt"
        missing = [path.name for path in (encoder, decoder, joiner, tokens) if not path.exists()]
        if missing:
            raise RuntimeError(f"Sherpa ONNX model is incomplete at {self.model_dir}: missing {', '.join(missing)}")

        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            tokens=str(tokens),
            num_threads=self.num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=1.2,
            rule3_min_utterance_length=18.0,
        )
        self.stream = self.recognizer.create_stream()
        self.last_partial = ""

    def accept_audio(self, pcm: bytes) -> list[ASREvent]:
        if self.recognizer is None or self.stream is None:
            return [ASREvent(type="error", text="", error="Sherpa ONNX recognizer is not started.")]
        if not pcm:
            return []

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return []

        events: list[ASREvent] = []
        self.stream.accept_waveform(SAMPLE_RATE, samples)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)

        partial = self._result_text()
        if partial and partial != self.last_partial:
            self.last_partial = partial
            events.append(ASREvent(type="partial", text=partial, source="sherpa_onnx"))

        if self.recognizer.is_endpoint(self.stream):
            final_text = self._result_text()
            if final_text:
                events.append(ASREvent(type="final", text=final_text, source="sherpa_onnx"))
            self.recognizer.reset(self.stream)
            self.last_partial = ""
        return events

    def finalize(self) -> list[ASREvent]:
        if self.recognizer is None or self.stream is None:
            return []
        self.stream.input_finished()
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = self._result_text()
        self.reset()
        return [ASREvent(type="final", text=text, source="sherpa_onnx")] if text else []

    def _result_text(self) -> str:
        if self.recognizer is None or self.stream is None:
            return ""
        result = self.recognizer.get_result(self.stream)
        if isinstance(result, str):
            return result.strip()
        return str(getattr(result, "text", "") or "").strip()

    def reset(self) -> None:
        if self.recognizer is not None and self.stream is not None:
            self.recognizer.reset(self.stream)
        self.last_partial = ""

    def close(self) -> None:
        self.stream = None
        self.recognizer = None
        self.last_partial = ""
