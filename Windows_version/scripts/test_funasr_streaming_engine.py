from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np

from asr.funasr_streaming_engine import FunASRStreamingEngine


SAMPLE_RATE = 16000
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * CHUNK_MS // 1000 * 2


def read_wav_as_16k_mono_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        framerate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width != 2:
        raise ValueError("Only 16-bit PCM wav files are supported by this lightweight test script.")

    audio = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)

    if framerate != SAMPLE_RATE:
        try:
            from scipy.signal import resample_poly
        except Exception as exc:
            raise RuntimeError("scipy is required to resample non-16k wav files.") from exc
        gcd = np.gcd(framerate, SAMPLE_RATE)
        audio = resample_poly(audio.astype(np.float32), SAMPLE_RATE // gcd, framerate // gcd)
        audio = np.clip(audio, -32768, 32767).astype(np.int16)

    return audio.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="Test FlowVoice FunASR streaming engine with a wav file.")
    parser.add_argument("wav", type=Path, help="Path to wav file.")
    parser.add_argument("--hotwords", default="", help="Optional hotwords passed to FunASR.")
    parser.add_argument("--chunk-ms", type=int, default=600, help="Streaming partial chunk interval in ms.")
    args = parser.parse_args()

    pcm = read_wav_as_16k_mono_pcm(args.wav)
    engine = FunASRStreamingEngine(hotwords=args.hotwords, target_chunk_ms=args.chunk_ms)
    engine.start()

    for offset in range(0, len(pcm), CHUNK_BYTES):
        chunk = pcm[offset : offset + CHUNK_BYTES]
        for event in engine.accept_audio(chunk):
            if event.type == "partial":
                print(f"[partial] {event.text}")
            elif event.type == "error":
                print(f"[error] {event.error}")

    for event in engine.finalize():
        if event.type == "final":
            print(f"[final] {event.text}")
        elif event.type == "error":
            print(f"[error] {event.error}")

    engine.close()


if __name__ == "__main__":
    main()
